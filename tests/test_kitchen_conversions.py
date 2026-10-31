# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import tempfile
import unittest
from core.kitchen_conversions import image_to_dat,dat_to_image


class KitchenConversionTests(unittest.TestCase):
    def test_real_raw_sparse_dat_raw_round_trip(self):
        with tempfile.TemporaryDirectory(prefix='rak-dat-test-') as temporary:
            root=Path(temporary);image=root/'system.img';data=bytes(range(256))*256
            image.write_bytes(data)
            dat=image_to_dat(str(image))
            self.assertTrue(Path(dat).is_file())
            self.assertTrue((root/'system.transfer.list').is_file())
            self.assertFalse(list(root.glob('.rak-convert-*')))
            image.unlink()
            rebuilt=dat_to_image(dat)
            self.assertEqual(Path(rebuilt).read_bytes(),data)


if __name__=='__main__':unittest.main()
