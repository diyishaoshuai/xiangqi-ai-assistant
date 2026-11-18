import unittest
from unittest.mock import Mock
import threading
from dataclasses import replace
import cv2
import numpy as np
from PIL import Image, ImageDraw
from recognition import BoardGeometry
from grid_geometry import audit_grid, refine_grid_lines
from app import XiangqiApp


class GenericGridTests(unittest.TestCase):
    def fixture(self, scale=1, dark=False, perspective=False):
        image = Image.new("RGB", (450,500), "#243c48" if dark else "#ecd7b0")
        draw = ImageDraw.Draw(image)
        ink = "#dadada" if dark else "#392819"
        for x in range(9):
            px = 50+x*350/8
            draw.line((px,50,px,450), fill=ink,width=2)
        for y in range(10):
            py=50+y*400/9
            draw.line((50,py,400,py),fill=ink,width=2)
        matrix = np.array([[scale, .04*scale if perspective else 0, 10],
                           [.02*scale if perspective else 0,scale,10],
                           [.00004 if perspective else 0,0,1]],dtype=float)
        size=(round(490*scale+20),round(550*scale+20))
        image=Image.fromarray(cv2.warpPerspective(np.asarray(image),matrix,size))
        geometry=BoardGeometry(tuple(matrix.reshape(-1)),False,.99,size)
        return image,geometry

    def test_theme_scale_and_perspective_do_not_require_platform_profile(self):
        for scale in (.7,1,2):
            for dark in (False,True):
                for perspective in (False,True):
                    with self.subTest(scale=scale,dark=dark,perspective=perspective):
                        image,g=self.fixture(scale,dark,perspective)
                        self.assertTrue(audit_grid(image,g,{}).safe)

    def test_wrong_projection_and_blank_image_are_rejected(self):
        image,g=self.fixture()
        matrix=np.array(g.inverse_matrix).reshape(3,3).copy()
        matrix[0,2]+=20
        matrix[1,2]+=20
        wrong=BoardGeometry(tuple(matrix.reshape(-1)),False,.99,image.size)
        self.assertFalse(audit_grid(image,wrong,{}).safe)
        self.assertFalse(audit_grid(Image.new('RGB',image.size),g,{}).safe)

    def test_grid_lines_repair_translation_and_scale_before_classification(self):
        image,g=self.fixture()
        matrix=np.array(g.inverse_matrix).reshape(3,3).copy()
        matrix[0,2]+=15
        matrix[1,1]*=1.08
        wrong=replace(g,inverse_matrix=tuple(matrix.reshape(-1)))
        self.assertFalse(audit_grid(image,wrong,{}).safe)
        corrected=refine_grid_lines(image,wrong)
        self.assertTrue(audit_grid(image,corrected,{}).safe)
        for square in ((0,0),(8,9),(7,0),(7,8)):
            self.assertLess(np.linalg.norm(np.array(corrected.point_for_square(square))-g.point_for_square(square)),2)

    def test_complete_grid_allows_one_missing_source_disc_center(self):
        image,g=self.fixture()
        squares={(0,0):'R',(2,0):'B',(4,0):'K',(6,0):'B',(8,0):'R',
                 (0,3):'P',(2,3):'P',(4,3):'P',(6,3):'P',(8,3):'P',
                 (0,6):'p',(2,6):'p',(4,6):'p',(6,6):'p',(8,6):'p',
                 (0,9):'r',(2,9):'b',(4,9):'k',(6,9):'b',(8,9):'r',
                 (6,2):'N'}
        source=(6,2)
        centers=tuple((x,y,*g.point_for_square((x,y))) for x,y in squares if (x,y)!=source)
        geometry=replace(g,piece_centers=centers)
        audit=audit_grid(image,geometry,squares,source)
        self.assertTrue(audit.safe, audit)
        self.assertIn("网格交点",audit.reason)
        self.assertGreaterEqual(audit.anchor_fraction,.80)

    def test_missing_source_center_does_not_override_weak_global_evidence(self):
        image,g=self.fixture()
        source=(6,2)
        board={(0,0):'R',(2,0):'B',(4,0):'K',(6,0):'B',(8,0):'R',
               (0,9):'r',(2,9):'b',(4,9):'k',(6,9):'b',(8,9):'r',source:'N'}
        centers=tuple((x,y,*g.point_for_square((x,y))) for x,y in board if (x,y)!=source)
        # Removing the middle of most lines leaves some plausible segments,
        # but not enough distributed anchor evidence for a safe fallback.
        blank=Image.new('RGB',image.size,'#ecd7b0')
        audit=audit_grid(blank,replace(g,piece_centers=centers),board,source)
        self.assertFalse(audit.safe)

    def test_click_audit_cancels_before_capture(self):
        app=object.__new__(XiangqiApp)
        app._mouse_autoplay_cancelled=Mock(return_value=True)
        app._grab_screen=Mock()
        with self.assertRaises(InterruptedError):
            app._audit_click_frame(1,threading.Event(),{},(0,0))
        app._grab_screen.assert_not_called()

    def test_click_audit_never_accepts_changed_board(self):
        image,g=self.fixture()
        app=object.__new__(XiangqiApp)
        app._mouse_autoplay_cancelled=Mock(return_value=False)
        app._grab_screen=Mock(return_value=image)
        app.mouse_auto_frame_cache=Mock()
        app.recognizer=Mock()
        app.logger=Mock()
        app.diagnostic_recorder=Mock()
        app._capture_mouse_board=Mock(return_value=({},None,g))
        with self.assertRaises(RuntimeError):
            app._audit_click_frame(1,threading.Event(),{(0,0):'R'},(0,0))
        app.mouse_auto_frame_cache.clear.assert_called_once()
