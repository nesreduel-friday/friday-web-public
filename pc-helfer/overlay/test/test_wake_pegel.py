import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wecken import pegel_reicht


def test_leises_mikro_mit_ruhe_wird_erkannt():
    assert pegel_reicht(spitze=0.012, boden=0.002)


def test_wort_im_grundrauschen_wird_verworfen():
    assert not pegel_reicht(spitze=0.010, boden=0.005)


def test_unter_der_untergrenze_wird_verworfen():
    assert not pegel_reicht(spitze=0.004, boden=0.0005)
