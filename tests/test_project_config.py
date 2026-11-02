# SPDX-License-Identifier: MPL-2.0
import os
import tempfile
import pytest
from core.project_config import (
    setup_project_container,
    load_project_config,
    resolve_tree_target,
    RAK_PROJECT_FILE,
    RAK_PROJECT_INFO,
)


def test_setup_single_local():
    with tempfile.TemporaryDirectory() as tmpdir:
        proj_root = os.path.join(tmpdir, "MySingleProj")
        cfg = setup_project_container(
            proj_root=proj_root,
            project_name="MySingleProj",
            mode=1,
            p1="port_a"
        )
        assert os.path.isdir(proj_root)
        assert os.path.isfile(os.path.join(proj_root, RAK_PROJECT_FILE))
        assert os.path.isfile(os.path.join(proj_root, RAK_PROJECT_INFO))
        assert os.path.isdir(os.path.join(proj_root, "port_a", "config"))

        read_cfg = load_project_config(proj_root)
        assert read_cfg["project_name"] == "MySingleProj"
        assert read_cfg["trees"]["port_a"]["type"] == "local"
        assert not read_cfg["has_imported_trees"]

        resolved = resolve_tree_target(proj_root, "port_a")
        assert resolved == os.path.abspath(os.path.join(proj_root, "port_a"))


def test_setup_single_imported():
    with tempfile.TemporaryDirectory() as tmpdir:
        external_rom = os.path.join(tmpdir, "ExistingRomProgress")
        os.makedirs(external_rom, exist_ok=True)
        with open(os.path.join(external_rom, "build.prop"), "w") as f:
            f.write("ro.build.version.release=14\n")

        proj_root = os.path.join(tmpdir, "ImportedProj")
        cfg = setup_project_container(
            proj_root=proj_root,
            project_name="ImportedProj",
            mode=1,
            p1="my_rom",
            target1=external_rom
        )
        assert os.path.isfile(os.path.join(proj_root, RAK_PROJECT_FILE))
        assert os.path.isfile(os.path.join(proj_root, RAK_PROJECT_INFO))

        read_cfg = load_project_config(proj_root)
        assert read_cfg["has_imported_trees"] is True
        assert read_cfg["trees"]["my_rom"]["type"] == "imported"
        assert os.path.abspath(read_cfg["trees"]["my_rom"]["target"]) == os.path.abspath(external_rom)

        # Resolution resolves to external imported dir
        resolved = resolve_tree_target(proj_root, "my_rom")
        assert resolved == os.path.abspath(external_rom)

        # Config directory inside imported target was ensured
        assert os.path.isdir(os.path.join(external_rom, "config"))

        # Verify junction or target marker exists
        link_path = os.path.join(proj_root, "my_rom")
        assert os.path.exists(link_path)


def test_setup_dual_mixed():
    with tempfile.TemporaryDirectory() as tmpdir:
        ext_port1 = os.path.join(tmpdir, "ExtBaseRom")
        os.makedirs(ext_port1, exist_ok=True)

        proj_root = os.path.join(tmpdir, "DualMixedProj")
        cfg = setup_project_container(
            proj_root=proj_root,
            project_name="DualMixedProj",
            mode=2,
            p1="base_rom",
            p2="port_rom",
            target1=ext_port1,
            target2=None
        )
        read_cfg = load_project_config(proj_root)
        assert read_cfg["has_imported_trees"] is True
        assert read_cfg["trees"]["base_rom"]["type"] == "imported"
        assert read_cfg["trees"]["port_rom"]["type"] == "local"

        # resolve_tree_target check
        assert resolve_tree_target(proj_root, "base_rom") == os.path.abspath(ext_port1)
        assert resolve_tree_target(proj_root, "port_rom") == os.path.abspath(os.path.join(proj_root, "port_rom"))

        # Local port_rom config directory exists in container
        assert os.path.isdir(os.path.join(proj_root, "port_rom", "config"))


def test_setup_dual_both_imported():
    with tempfile.TemporaryDirectory() as tmpdir:
        ext1 = os.path.join(tmpdir, "Rom1")
        ext2 = os.path.join(tmpdir, "Rom2")
        os.makedirs(ext1, exist_ok=True)
        os.makedirs(ext2, exist_ok=True)

        proj_root = os.path.join(tmpdir, "DualBothProj")
        cfg = setup_project_container(
            proj_root=proj_root,
            project_name="DualBothProj",
            mode=2,
            p1="rom_a",
            p2="rom_b",
            target1=ext1,
            target2=ext2
        )
        read_cfg = load_project_config(proj_root)
        assert read_cfg["has_imported_trees"] is True
        assert read_cfg["trees"]["rom_a"]["type"] == "imported"
        assert read_cfg["trees"]["rom_b"]["type"] == "imported"

        assert resolve_tree_target(proj_root, "rom_a") == os.path.abspath(ext1)
        assert resolve_tree_target(proj_root, "rom_b") == os.path.abspath(ext2)

        # Container attention file contains descriptions of both imports
        with open(os.path.join(proj_root, RAK_PROJECT_INFO), "r", encoding="utf-8") as f:
            content = f.read()
        assert "Imported ->" in content
        assert "rom_a" in content
        assert "rom_b" in content
