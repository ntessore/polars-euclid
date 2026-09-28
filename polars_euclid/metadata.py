import os.path
import xml.etree.ElementTree as ET

from dataclasses import dataclass
from functools import reduce


@dataclass
class CatalogDescription:
    """Data from CatalogDescription elements."""
    name: str
    type: str
    origin: str
    fits: str
    hdu: int


def read_metadata(
    paths: list[str],
    *,
    data_path: str = "data",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> dict[int, list[CatalogDescription]]:
    """Collect all data files from the given set of XML products."""

    catalogs: dict[int, list[CatalogDescription]] = {}

    for path in paths:
        root = ET.parse(path).getroot()

        for tile_index_xpath in ["./Data/TileIndex", "./Data/TileIndexList"]:
            tile_index_node = root.find(tile_index_xpath)
            if tile_index_node is not None:
                break
        else:
            raise ValueError(f"{path}: missing tile index")

        tile_index = int(tile_index_node.text)

        if tile_index not in catalogs:
            catalogs[tile_index] = []

        for catalog_description in root.findall("./Data/CatalogDescription"):
            catalog_name = catalog_description.find("CatalogName").text

            if include is not None and catalog_name not in include:
                continue
            if exclude is not None and catalog_name in exclude:
                continue

            catalog_type = catalog_description.find("CatalogType").text
            catalog_origin = catalog_description.find("CatalogOrigin").text
            catalog_path = catalog_description.find("PathToCatalogFile").text
            catalog_hdu = int(catalog_description.find("CatalogFormatHDU").text)

            catalog_fits = root.find("./" + "/".join(catalog_path.split("."))).text
            catalog_fits = os.path.join(data_path, catalog_fits)

            catalogs[tile_index].append(
                CatalogDescription(
                    name=catalog_name,
                    type=catalog_type,
                    origin=catalog_origin,
                    fits=catalog_fits,
                    hdu=catalog_hdu,
                )
            )

    return catalogs


def check_catalogs(catalogs: dict[int, list[CatalogDescription]]) -> None:
    """Ensure that a set of catalogues is consistent."""
    catalog_names = [{catalog.name for catalog in tile} for tile in catalogs.values()]
    combined_names = reduce(lambda x, y: x | y, catalog_names)

    missing = reduce(lambda x, y: x | y, (combined_names - names for names in catalog_names))
    if missing:
        raise ValueError("inconsistent catalogues: " + ", ".join(sorted(missing)))

    # TODO: check consistency of catalogue type and origin?
