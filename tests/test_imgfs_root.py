import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from core import imgfs, native_backend, rak_img_cli


class ImageRootTests(unittest.TestCase):
    def erofs_reader(self):
        reader = SimpleNamespace(root_nid=56, closed=False)

        def _close():
            reader.closed = True

        reader.close = _close
        reader.f = SimpleNamespace(close=_close)
        reader.stat_nid = mock.Mock(
            return_value={
                "path": "/",
                "nid": 56,
                "size": 512,
                "mode": 0o40755,
                "is_dir": True,
                "is_link": False,
                "selinux": "u:object_r:rootfs:s0",
            }
        )
        reader.walk = mock.Mock(side_effect=AssertionError("root is absent from walk"))
        return reader

    def test_erofs_root_is_resolved_without_walking_children(self):
        reader = self.erofs_reader()
        with mock.patch.object(imgfs, "open_reader", return_value=("EROFS", reader)):
            result, node = imgfs.resolve_node("image.img", "/")
        self.assertIs(result, reader)
        self.assertEqual(node.ino, 56)
        self.assertEqual(node.selinux, "u:object_r:rootfs:s0")
        self.assertTrue(node.is_dir)

    def test_ext_root_uses_inode_two(self):
        reader = SimpleNamespace(f=io.BytesIO())
        reader._read_inode = mock.Mock(return_value=(0o40755, 4096, 0, [], b"inode"))
        reader._inode_selinux = mock.Mock(return_value="u:object_r:rootfs:s0")
        with mock.patch.object(imgfs, "open_reader", return_value=("EXT", reader)):
            _, _, node = imgfs._resolve_writable("image.img", "/")
        reader._read_inode.assert_called_once_with(2)
        self.assertEqual(node.ino, 2)

    def test_root_selinux_write_reaches_root_inode(self):
        with mock.patch.object(imgfs, "detect_fs", return_value="EROFS"), \
             mock.patch.object(imgfs, "_is_sparse_file", return_value=False), \
             mock.patch.object(native_backend, "erofs_edit_selinux", return_value="u:object_r:rootfs:s0") as write:
            res = imgfs._set_selinux_unlocked("image.img", "/", "u:object_r:rootfs:s0")
        write.assert_called_once_with("image.img", "/", "u:object_r:rootfs:s0")
        self.assertEqual(res, "u:object_r:rootfs:s0")

    def test_stat_accepts_directory_without_reading_its_file_data(self):
        reader = self.erofs_reader()
        node = imgfs._root_node("EROFS", reader)
        with mock.patch.object(rak_img_cli, "_read_host_path", return_value="image.img"), \
             mock.patch("os.path.isfile", return_value=True), \
             mock.patch.object(imgfs, "resolve_node", return_value=(reader, node)), \
             mock.patch.object(imgfs, "read_path", side_effect=AssertionError("directory data read")):
            ok, result = rak_img_cli.cmd_stat(["image.img", "/", "--json"], ".")
        self.assertTrue(ok)
        self.assertEqual(json.loads(result)["selinux"], "u:object_r:rootfs:s0")
        self.assertTrue(reader.closed)

    def test_failed_root_read_closes_reader_and_logs(self):
        reader = self.erofs_reader()
        reader.stat_nid.side_effect = ValueError("damaged inode")
        with mock.patch.object(imgfs, "open_reader", return_value=("EROFS", reader)), \
             mock.patch("logging.exception") as log:
            with self.assertRaises(ValueError):
                imgfs.resolve_node("image.img", "/")
        self.assertTrue(reader.closed)
        log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
