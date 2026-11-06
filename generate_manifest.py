#!/usr/bin/env python3
"""Generate manifest.json listing mods, resourcepacks and shaderpacks under a directory.

Usage:
    generate_manifest.py --path . --loader neoforge --version 1.0.1 \\
        --game-version 1.21.1 --base-url https://url/ \\
        --output manifest.json
"""
import argparse
import hashlib
import json
import re
import sys
import tomllib
import urllib.parse
import zipfile
from pathlib import Path

VERSION_PLACEHOLDER_RE = re.compile(r"^\$\{.*\}$")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_placeholder_version(zf: zipfile.ZipFile) -> str | None:
    try:
        manifest = zf.read("META-INF/MANIFEST.MF").decode("utf-8", "ignore")
    except KeyError:
        return None
    for line in manifest.splitlines():
        if line.startswith("Implementation-Version:"):
            return line.split(":", 1)[1].strip()
    return None


def read_fabric_metadata(zf: zipfile.ZipFile) -> tuple[str, str] | None:
    try:
        raw = zf.read("fabric.mod.json")
    except KeyError:
        return None
    try:
        data = json.loads(raw, strict=False)
    except json.JSONDecodeError:
        return None
    return data.get("id"), data.get("version")


def read_forge_metadata(zf: zipfile.ZipFile) -> tuple[str, str] | None:
    for candidate in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
        try:
            raw = zf.read(candidate)
        except KeyError:
            continue
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except tomllib.TOMLDecodeError:
            continue
        mods = data.get("mods") or []
        if not mods:
            continue
        return mods[0].get("modId"), mods[0].get("version")
    return None


def extract_metadata(jar_path: Path, loader: str) -> tuple[str, str]:
    if loader == "vanilla":
        return jar_path.stem, "unknown"

    with zipfile.ZipFile(jar_path) as zf:
        result = None
        if loader == "fabric":
            result = read_fabric_metadata(zf)
        elif loader in ("forge", "neoforge"):
            result = read_forge_metadata(zf)
        else:
            result = read_fabric_metadata(zf) or read_forge_metadata(zf)

        if result is None:
            print(f"warning: no mod metadata found in {jar_path.name}", file=sys.stderr)
            return jar_path.stem, "unknown"

        mod_id, version = result
        mod_id = mod_id or jar_path.stem
        if not version or VERSION_PLACEHOLDER_RE.match(version):
            resolved = resolve_placeholder_version(zf)
            if resolved:
                version = resolved
            else:
                print(f"warning: could not resolve version for {jar_path.name}", file=sys.stderr)
                version = version or "unknown"
        return mod_id, version


def find_subfolder(base_path: Path, aliases: list[str]) -> Path | None:
    """Finds a matching subfolder among possible aliases."""
    for alias in aliases:
        candidate = base_path / alias
        if candidate.is_dir():
            return candidate
    return None


def build_category_url(base_url: str, category: str, filename: str) -> str:
    """Constructs URL for a file in a category without duplicating folder names."""
    clean_base = base_url.rstrip("/")
    if clean_base.endswith(f"/{category}"):
        clean_base = clean_base[: -len(category) - 1]
    elif clean_base.endswith("/mods") or clean_base.endswith("/resourcepacks") or clean_base.endswith("/shaderpacks"):
        clean_base = clean_base.rsplit("/", 1)[0]
    clean_base = clean_base.rstrip("/") + "/"
    return f"{clean_base}{category}/{urllib.parse.quote(filename)}"


def process_mods(folder: Path | None, loader: str, base_url: str) -> list[dict]:
    if folder is None:
        return []
    mods = []
    for jar_path in sorted(folder.glob("*.jar")):
        mod_id, mod_version = extract_metadata(jar_path, loader)
        mods.append(
            {
                "id": mod_id,
                "name": jar_path.name,
                "version": mod_version,
                "sha256": sha256_of(jar_path),
                "url": build_category_url(base_url, "mods", jar_path.name),
            }
        )
    mods.sort(key=lambda f: f["id"])
    return mods


def process_resourcepacks(folder: Path | None, default_version: str, base_url: str) -> list[dict]:
    if folder is None:
        return []
    packs = []
    for rp_path in sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".zip", ".rar")]):
        pack_id = rp_path.stem.lower().replace(" ", "_").replace("-", "_")
        packs.append(
            {
                "id": pack_id,
                "name": rp_path.name,
                "version": default_version,
                "sha256": sha256_of(rp_path),
                "url": build_category_url(base_url, "resourcepacks", rp_path.name),
            }
        )
    packs.sort(key=lambda f: f["id"])
    return packs


def process_shaderpacks(folder: Path | None, default_version: str, base_url: str) -> list[dict]:
    if folder is None:
        return []
    shaders = []
    for sp_path in sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".zip", ".rar")]):
        shader_id = sp_path.stem.lower().replace(" ", "_").replace("-", "_")
        shaders.append(
            {
                "id": shader_id,
                "name": sp_path.name,
                "version": default_version,
                "sha256": sha256_of(sp_path),
                "url": build_category_url(base_url, "shaderpacks", sp_path.name),
            }
        )
    shaders.sort(key=lambda f: f["id"])
    return shaders


def build_manifest(path: Path, loader: str, version: str, game_version: str, base_url: str) -> dict:
    mods_dir = find_subfolder(path, ["mods", "mod"])
    rp_dir = find_subfolder(path, ["resourcepacks", "resourcepack", "textures"])
    sp_dir = find_subfolder(path, ["shaderpacks", "shaderpack", "shaders"])

    # Fallback legacy: si la carpeta pasada directamente contiene .jar y no tiene subcarpetas
    if mods_dir is None and rp_dir is None and sp_dir is None:
        direct_jars = list(path.glob("*.jar"))
        if direct_jars or path.name.lower() in ("mods", "mod"):
            mods_dir = path

    mods_list = process_mods(mods_dir, loader, base_url)
    rp_list = process_resourcepacks(rp_dir, version, base_url)
    sp_list = process_shaderpacks(sp_dir, version, base_url)

    return {
        "version": version,
        "gameVersion": game_version,
        "loader": loader,
        "files": {
            "mods": mods_list,
            "resourcepacks": rp_list,
            "shaderpacks": sp_list,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path, help="Directory containing mods, resourcepacks and/or shaderpacks folders")
    parser.add_argument("--loader", required=True, choices=["fabric", "forge", "neoforge", "vanilla"])
    parser.add_argument("--version", required=True, help="Manifest/modpack version")
    parser.add_argument("--game-version", required=True, help="Minecraft version")
    parser.add_argument("--base-url", required=True, help="Base URL where the files are hosted")
    parser.add_argument("--output", default="manifest.json", type=Path)
    args = parser.parse_args()

    if not args.path.is_dir():
        parser.error(f"{args.path} is not a directory")

    manifest = build_manifest(args.path, args.loader, args.version, args.game_version, args.base_url)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")

    files_obj = manifest["files"]
    n_mods = len(files_obj["mods"])
    n_rp = len(files_obj["resourcepacks"])
    n_sp = len(files_obj["shaderpacks"])
    total = n_mods + n_rp + n_sp
    print(f"wrote {args.output} with {total} files ({n_mods} mods, {n_rp} resourcepacks, {n_sp} shaderpacks)")


if __name__ == "__main__":
    main()
