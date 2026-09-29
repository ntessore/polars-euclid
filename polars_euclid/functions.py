import glob
import os.path

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import polars as pl
import fitsio

from polars.io.plugins import register_io_source

from polars_euclid.metadata import CatalogDescription, read_metadata, check_catalogs


ALLOWED_JOIN = ["inner", "full"]


@dataclass
class Catalog:
    """Describes a FITS catalogue with HDU and column names."""
    fits: str
    hdu: int
    columns: list[str]


def expand_paths(paths: str | list[str]) -> list[str]:
    """Expand all paths with glob and directory scanning."""
    if isinstance(paths, str):
        paths = [paths]
    out = []
    for path in paths:
        expanded_paths = glob.glob(path)
        if not expanded_paths:
            raise FileNotFoundError(path)
        for expanded_path in expanded_paths:
            if os.path.isdir(expanded_path):
                out.extend(
                    glob.iglob(
                        os.path.join(expanded_path, "**", "*.xml"),
                        recursive=True,
                    ),
                )
            else:
                out.append(expanded_path)
    return out


def join_catalogs(
    left: pl.DataFrame | None,
    right: pl.DataFrame,
    join: str,
) -> pl.DataFrame:
    """Join two catalogues on OBJECT_ID."""
    if left is None:
        return right
    return left.join(right, on="OBJECT_ID", how=join, coalesce=True)


def read_schema(
    catalogs: list[CatalogDescription],
    *,
    join: str = "full",
) -> tuple[pl.Schema | None, dict[str, list[str]]]:
    """
    Read schema from catalogues.
    Returns the schema and a list of columns for each catalogue.
    """
    df: pl.DataFrame | None = None
    columns: dict[str, list[str]] = {}
    for catalog in catalogs:
        df = join_catalogs(
            df,
            pl.from_numpy(fitsio.read(catalog.fits, ext=catalog.hdu, rows=[])),
            join,
        )
        columns[catalog.name] = df.columns
    return df.schema if df is not None else None, columns


def read_catalogs(
    catalogs: list[Catalog],
    *,
    with_columns: list[str] | str | None = None,
    join: str = "full",
) -> pl.DataFrame | None:
    """Read all catalogues for a given tile."""
    df: pl.DataFrame | None = None
    for catalog in catalogs:
        if with_columns is None:
            columns = None
        else:
            columns = [column for column in catalog.columns if column in with_columns]
        df = join_catalogs(
            df,
            pl.from_numpy(fitsio.read(catalog.fits, ext=catalog.hdu, columns=columns)),
            join,
        )
    return df


def combined_catalogs_columns(
    catalogs: dict[int, list[CatalogDescription]],
    columns: dict[str, list[str]],
) -> dict[int, list[Catalog]]:
    """Sort and update catalogues with column information."""
    out: dict[int, list[Catalog]] = {}
    for tile_index, tile_catalogs in catalogs.items():
        updated_catalogs: list[Catalog] = []
        for catalog_name, catalog_columns in columns.items():
            try:
                [catalog] = [
                    catalog
                    for catalog in tile_catalogs
                    if catalog.name == catalog_name
                ]
            except ValueError:
                msg = f"invalid {catalog_name} catalog for tile {tile_index}"
                raise ValueError(msg) from None

            updated_catalogs.append(
                Catalog(
                    fits=catalog.fits,
                    hdu=catalog.hdu,
                    columns=catalog_columns,
                )
            )
        out[tile_index] = updated_catalogs
    return out


def scan_euclid(
    paths: str | list[str],
    *,
    data_path: str = "data",
    join="full",
    include_catalog: list[str] | None = None,
    exclude_catalog: list[str] | None = None,
) -> pl.LazyFrame:
    """Read Euclid data products."""

    if join not in ALLOWED_JOIN:
        raise ValueError("join must be one of " + ", ".join(ALLOWED_JOIN))

    xml_paths = expand_paths(paths)
    if not xml_paths:
        raise FileNotFoundError(str(paths))

    catalogs = read_metadata(
        xml_paths,
        data_path=data_path,
        include=include_catalog,
        exclude=exclude_catalog,
    )

    check_catalogs(catalogs)

    schema, columns = read_schema(next(iter(catalogs.values())), join=join)

    if schema is None:
        raise FileNotFoundError("no catalog found")

    catalogs_columns = combined_catalogs_columns(catalogs, columns)

    def source_generator(
        with_columns: list[str] | None,
        predicate: pl.Expr | None,
        n_rows: int | None,
        batch_size: int | None,
    ) -> Iterator[pl.DataFrame]:
        """
        Generator function that creates the source.
        This function will be registered as IO source.
        """

        for tile_index, tile_catalogs in catalogs_columns.items():
            if n_rows is not None and n_rows < 1:
                break

            df = read_catalogs(tile_catalogs, with_columns=with_columns, join=join)

            if predicate is not None:
                df = df.filter(predicate)

            yield df

            if n_rows is not None:
                n_rows -= df.height

    return register_io_source(
        io_source=source_generator,
        schema=schema,
        validate_schema=True,
        is_pure=True,
    )
