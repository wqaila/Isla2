"""Checks for atlas import errors that would erase Qinglan's green robe or timing."""
from pathlib import Path
import sys
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
from prepare_pet_badge_assets import CELL, extract, place, resample


class PetAtlasTests(unittest.TestCase):
    def test_empty_cell_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Empty source cell"):
            extract(Image.new("RGBA", (1536, 2288)), 0, 0)

    def test_transparent_import_keeps_green_robes(self):
        cell = Image.new("RGBA", CELL)
        ImageDraw.Draw(cell).rectangle((70, 10, 120, 200), fill=(10, 200, 50, 255))
        result = place(cell, 0.95, (89, 117))
        pixel = result.getpixel((180, 200))
        self.assertEqual(pixel[3], 255)
        self.assertGreater(pixel[1], 180)

    def test_overwide_artwork_is_rejected_before_cropping(self):
        with self.assertRaisesRegex(ValueError, "safe board sprite bounds"):
            place(Image.new("RGBA", CELL, (10, 200, 50, 255)), 1, (84, 105))

    def test_resampling_preserves_long_final_hold(self):
        a = Image.new("RGBA", (1, 1), "red")
        b = Image.new("RGBA", (1, 1), "blue")
        frames = resample([a, b], [100, 300])
        self.assertEqual(len(frames), 10)
        self.assertIs(frames[-1], b)
        self.assertEqual(sum(f is b for f in frames), 8)


if __name__ == "__main__":
    unittest.main()
