import unittest
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from recognition import NeuralBoardRecognizer
from grid_geometry import GridAudit


class CandidateGridTests(unittest.TestCase):
    def make_recognizer(self):
        r=object.__new__(NeuralBoardRecognizer)
        r.pose=Mock()
        r.pose.pred.return_value=(np.float32([[50,50],[400,50],[50,450],[400,450]]),np.full(4,.8))
        r.classifier=Mock()
        rows=[list('.'*9) for _ in range(10)]
        rows[0][4]='k'
        rows[9][4]='K'
        counter=[0]
        def classify(*args,**kwargs):
            counter[0]+=1
            r.classifier.last_scores=np.full((90,16),counter[0])
            return None,rows,np.full((10,9),.99),'test'
        r.classifier.pred.side_effect=classify
        r._candidate_bboxes=Mock(return_value=[[0,0,500,500],[0,0,600,500]])
        r._tiled_candidate_bboxes=Mock(return_value=[])
        r._refine_geometry_from_pieces=Mock(side_effect=lambda image,g,b:g)
        return r

    def test_high_classification_score_cannot_short_circuit_failed_geometry(self):
        r=self.make_recognizer()
        r._candidate_score=Mock(return_value=2.)
        with patch('grid_geometry.refine_grid_lines',side_effect=lambda image,g:g), \
             patch('grid_geometry.audit_grid',side_effect=[GridAudit(False,'bad'),GridAudit(True,'ok'),GridAudit(True,'ok')]):
            r.recognize(Image.new('RGB',(600,500)),require_grid=True,minimum_geometry_confidence=.1)
        self.assertEqual(r.pose.pred.call_count,2)
        self.assertEqual(r.last_search_bbox,(0,0,600,500))
        self.assertEqual(r.last_timings['grid_rejected'],1)
        # Validated transform is reused; no second fitting can change it later.
        self.assertEqual(r._refine_geometry_from_pieces.call_count,2)

    def test_rejected_later_candidate_does_not_replace_selected_probabilities(self):
        r=self.make_recognizer()
        r._candidate_score=Mock(return_value=1.)
        with patch('grid_geometry.refine_grid_lines',side_effect=lambda image,g:g), \
             patch('grid_geometry.audit_grid',side_effect=[GridAudit(True,'ok'),GridAudit(False,'bad'),GridAudit(True,'ok')]):
            r.recognize(Image.new('RGB',(600,500)),require_grid=True,minimum_geometry_confidence=.1)
        self.assertEqual(r.last_search_bbox,(0,0,500,500))
        self.assertTrue(np.all(r.classifier.last_scores==1))

    def test_all_candidates_rejected_returns_no_clickable_board(self):
        r=self.make_recognizer()
        with patch('grid_geometry.refine_grid_lines',side_effect=lambda image,g:g), \
             patch('grid_geometry.audit_grid',return_value=GridAudit(False,'bad')):
            with self.assertRaises(RuntimeError):
                r.recognize(Image.new('RGB',(600,500)),require_grid=True,minimum_geometry_confidence=.1)
        self.assertEqual(r.pose.pred.call_count,2)
