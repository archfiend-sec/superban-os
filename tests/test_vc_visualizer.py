import unittest
from pathlib import Path
import re


class TestVCVisualizer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html_path = Path(__file__).resolve().parent.parent / "web" / "static" / "index.html"
        cls.html_content = html_path.read_text(encoding="utf-8")

    def test_dom_elements_exist(self):
        """Verify the visualizer container, canvas, placeholder, and controls exist in DOM."""
        self.assertIn('id="vc-spectrum-container"', self.html_content)
        self.assertIn('id="vc-spectrum-canvas"', self.html_content)
        self.assertIn('id="vc-spectrum-placeholder"', self.html_content)
        self.assertIn('id="btn-toggle-vc-audio"', self.html_content)
        self.assertIn('id="vc-audio-status-pill"', self.html_content)
        self.assertIn('LIVE FREQUENCY BARS', self.html_content)

    def test_responsive_css_tablet_and_mobile(self):
        """Verify responsive CSS media queries for desktop, tablet, and mobile."""
        # Mobile CSS
        self.assertIn("#vc-spectrum-container {", self.html_content)
        self.assertIn("width: 100% !important;", self.html_content)

        # Tablet Cockpit CSS (768px - 1180px)
        start_idx = self.html_content.find("@media (min-width: 768px) and (max-width: 1180px)")
        self.assertNotEqual(start_idx, -1, "Tablet media query not found")
        end_idx = self.html_content.find("@media (min-width: 851px) and (max-width: 1180px)", start_idx)
        tablet_css = self.html_content[start_idx:end_idx] if end_idx != -1 else self.html_content[start_idx:start_idx + 2000]

        self.assertIn("#vc-audio-suite > div:first-child", tablet_css)
        self.assertIn("#vc-spectrum-container", tablet_css)
        self.assertIn("min-width: 140px !important;", tablet_css)

    def test_web_audio_analyser_configuration(self):
        """Verify Web Audio AnalyserNode is configured for voice spectrum resolution."""
        # fftSize should be 128 (64 bins, 375Hz/bin at 48kHz for speech clarity)
        self.assertIn("analyser.fftSize = 128;", self.html_content)
        self.assertIn("analyser.smoothingTimeConstant = 0.8;", self.html_content)
        self.assertIn("analyser.minDecibels = -90;", self.html_content)
        self.assertIn("analyser.maxDecibels = -10;", self.html_content)

    def test_three_honest_states_implementation(self):
        """Verify the three honest states: ACTIVE, IDLE, and DISCONNECTED/DORMANT."""
        # State 1: Active audio with energy threshold
        self.assertIn("const ENERGY_THRESHOLD = 3.5;", self.html_content)
        self.assertIn("avgEnergy >= ENERGY_THRESHOLD", self.html_content)
        self.assertIn("createLinearGradient", self.html_content)
        self.assertIn("#10b981", self.html_content)  # Emerald base
        self.assertIn("#f59e0b", self.html_content)  # Amber mid
        self.assertIn("#ef4444", self.html_content)  # Vermilion peak
        self.assertIn("vcVisualizerPeaks", self.html_content)  # Peak hold falloff

        # State 2: Idle stream (connected & listening, but silent)
        self.assertIn("LISTENING // OPEN CARRIER", self.html_content)
        self.assertIn("rgba(16, 185, 129, 0.25)", self.html_content)

        # State 3: Disconnected / Dormant
        self.assertIn("renderVCDormantSpectrum", self.html_content)
        self.assertIn("rgba(255, 255, 255, 0.08)", self.html_content)

    def test_frequency_bin_speech_mapping(self):
        """Verify non-linear speech bin mapping over 24 bars without overflow."""
        self.assertIn("const numBars = 24;", self.html_content)
        self.assertIn("Math.pow(t, 1.35)", self.html_content)
        # Ensure off-canvas clipping math (* 1.6 bug) is gone
        self.assertNotIn("(canvas.width / bufferLength) * 1.6", self.html_content)

    def test_dpr_retina_and_lifecycle(self):
        """Verify devicePixelRatio support, ResizeObserver, visibilitychange, and audio resume."""
        self.assertIn("window.devicePixelRatio", self.html_content)
        self.assertIn("ResizeObserver", self.html_content)
        self.assertIn("visibilitychange", self.html_content)
        self.assertIn("orientationchange", self.html_content)
        self.assertIn("window.vcAudioContext.state === 'suspended'", self.html_content)
        self.assertIn("window.vcAudioContext.resume()", self.html_content)


if __name__ == "__main__":
    unittest.main()
