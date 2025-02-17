# ------------------------------------------------------------------------------
# Copyright (c) 2022 Korawich Anuttra. All rights reserved.
# Licensed under the MIT License. See LICENSE in the project root for
# license information.
# ------------------------------------------------------------------------------
import time
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from typing import Any

import polars as pl
from azure.identity import InteractiveBrowserCredential
from elasticsearch import helpers
from elastic_transport import TlsError

from ..wrapper import Es, Index, extract_exception
from ..exceptions import RateLimitException
from .__conf import FRAMEWORK_SCD1_COLS, Metadata, DEFAULT_DT


def extract_delta_from_az(
    container: str,
    name: str,
    st_name: str,
    dest: Path,
):
    token = InteractiveBrowserCredential(
        authority="login.microsoftonline.com"
    ).get_token("https://storage.azure.com/.default").token

    lf: pl.LazyFrame = pl.scan_delta(
        f"az://{container}/{name}",
        storage_options={"account_name": st_name, "token": token},
    )

    print(
        f"Start extract data from '{container}/{name}' on {st_name!r} "
        f"with {lf.select(pl.len()).collect()} records"
    )

    lf.collect().write_delta(
        dest,
        mode="overwrite",
        delta_write_options={"schema_mode": "overwrite"},
    )


def prepare_row(row):
    return row


def create_actions(df: pl.DataFrame, id_col: str, index_name: str):

    for row in df.iter_rows(named=True):

        if row.pop('@updated', False):
            yield {
                "_op_type": "update",
                "_index": index_name,
                '_id': row.pop(id_col),
                'doc': prepare_row(row),
                'doc_as_upsert': True,
            }
        else:
            yield {
                "_op_type": "index",
                "_index": index_name,
                '_id': row.pop(id_col),
                **prepare_row(row),
            }


def bulk_load_task(
    df: pl.DataFrame,
    id_col: str,
    index_name: str,
    *,
    es: Es,
    request_timeout: int = 3800,
    retry_limit: int = 20,
) -> tuple[int, list, pl.DataFrame]:
    """Bulk load task for chucking of dataframe that has size limit with the
    bulk function.

    :rtype: tuple[int, list]
    """

    first_bulk_flag: bool = True
    retry_count: int = 0

    while first_bulk_flag or (retry_count > 0):
        print(
            f"({retry_count:02d}) Start running bulk load task ... ({len(df)})"
        )

        if retry_count > 0:
            time.sleep(60)

        if retry_count >= retry_limit:
            df.write_delta(
                f'../../data/issues/{index_name}-{uuid4()}',
                mode='overwrite',
            )
            print(
                "issue dataframe that retry reach limit the maximum value."
            )
            return 0, [], df

        first_bulk_flag: bool = False

        try:
            success, failed = helpers.bulk(
                es.client.options(request_timeout=request_timeout),
                actions=create_actions(
                    df,
                    id_col=id_col,
                    index_name=index_name,
                ),
                stats_only=False,
                refresh=False,
                raise_on_exception=False,
                raise_on_error=False,
            )
            print(
                f"[INFO]: ... Loading to {index_name} with status success: "
                f"{success} failed: {len(failed)}"
            )
            return success, failed, df

        except helpers.BulkIndexError:
            retry_count += 1
        except TlsError as err:
            print(f"TlsError: {err}")
            retry_count += 1
        except Exception as err:
            df.write_delta(
                f'../../data/issues/{index_name}-{uuid4()}',
                mode='overwrite',
            )
            print(f"{type(err)}: {err}")
            raise


def pl_asat_dt_to_datetime():
    return (
        pl.col("asat_dt")
        .cast(pl.String)
        .str
        .to_datetime("%Y%m%d", time_zone='UTC')
    )


def select_env(lf: pl.LazyFrame, metadata: Metadata, dev_env_flag: bool = True):
    if dev_env_flag:
        return (
            lf
            .select(
                pl.all().exclude(FRAMEWORK_SCD1_COLS).name.map(str.lower),
                pl.lit(False).alias('@updated'),
                (
                    pl.lit(metadata.asat_dt)
                    .cast(pl.String)
                    .str
                    .to_datetime("%Y%m%d", time_zone='UTC')
                    .alias('@upload_date')
                ),
                pl.lit(metadata.prcess_nm).alias("@upload_prcs_nm"),
                (
                    pl.when(pl.col("delete_f") == 1)
                    .then(True)
                    .otherwise(False)
                    .alias("@deleted")
                ),
            )
        )
    else:
        return (
            lf
            .select(
                pl.all().exclude(FRAMEWORK_SCD1_COLS).name.map(str.lower),
                (
                    pl.when(pl.col("updt_asat_dt").is_null())
                    .then(True)
                    .otherwise(False)
                    .alias("@updated")
                ),
                (
                    pl.concat_list(
                        (
                            pl_asat_dt_to_datetime(),
                            pl.coalesce(pl.col("updt_asat_dt"), DEFAULT_DT)
                        ),
                    ).list.max()
                    .dt.date()
                    .alias("@upload_date")
                ),
                pl.lit(metadata.prcess_nm).alias("@upload_prcs_nm"),
                (
                    pl.when(pl.col("delete_f") == 1)
                    .then(True)
                    .otherwise(False)
                    .alias("@deleted")
                ),
            )
        )


def retry_rate_limit(df: pl.DataFrame, es, index_nm) -> int:
    total_rows: int = len(df)
    total_success: int = 0
    for frame in df.iter_slices(n_rows=int(total_rows / 3)):
        print(
            f"(00) Start running retry bulk load task ... ({len(df)})"
        )
        success, failed, df = bulk_load_task(
            df=frame,
            id_col='es_id',
            index_name=index_nm,
            es=es,
        )
        if len(failed) > 0:
            raise NotImplementedError(
                'retry rate limit do not help on this case'
            )

        total_success += success
        time.sleep(15)

    if total_success != total_rows:
        raise ValueError(
            "it have somthing wrong on retry rate limit."
        )

    return total_success


def dump_delta_to_es(es: Es, metadata: Metadata, dev: bool = True):

    # NOTE: Extract data.
    lf: pl.LazyFrame = (
        pl.scan_delta(metadata.source)
        .pipe(select_env, metadata=metadata, dev_env_flag=dev)
    )

    success_total: int = 0
    failed_total: int = 0

    for main_lf in lf.collect(streaming=False).iter_slices(n_rows=metadata.limit_rows):

        with ThreadPoolExecutor(max_workers=metadata.limit_workers) as executor:

            futures: list[Future] = []

            for frame in main_lf.iter_slices(n_rows=metadata.limit_slice_rows):
                futures.append(
                    executor.submit(
                        bulk_load_task,
                        df=frame,
                        id_col='es_id',
                        index_name=metadata.index_nm,
                        es=es,
                    )
                )

                time.sleep(5)

            for future in as_completed(futures):
                success, failed, df = future.result()

                success_total += success

                if (num_failed := len(failed)) > 0:
                    try:
                        extract_exception(failed[0])
                    except RateLimitException:
                        success_total += retry_rate_limit(
                            df, es=es, index_nm=metadata.index_nm
                        )
                        num_failed = 0

                failed_total += num_failed

        print(f"[INFO]: ... Mark slice: {success_total}")
        time.sleep(25)

    print(success_total, failed_total)

    # NOTE: Remove all data that does not dump on the DEV
    if dev:
        index: Index = es.index(name=metadata.index_nm)
        index.refresh()

        rs = index.search_by_query(
            query={
                "bool": {
                    "must_not": [
                        {
                            "bool": {"filter":
                                [
                                    {"term": {"@upload_prcs_nm": metadata.prcess_nm}},
                                    {"range": {"@upload_date": {
                                        "gte": metadata.asat_dt_dash,
                                        "lte": metadata.asat_dt_dash,
                                        "format": "yyyy-MM-dd"
                                    }}},
                                ],
                            },
                        },
                    ],
                },
            },
            size=1000,
        )
        hits: list[Any] = rs.body['hits']['hits']
        records: int = len(hits)
        print(records)
        if records > 0 and records != success_total:
            print("Start delete doc that is not exists on the production.")
            rs = index.delete_by_query(
                query={
                    "bool": {
                        "must_not": [
                            {
                                "bool": {"filter":
                                    [
                                        {"term": {
                                            "@upload_prcs_nm": metadata.prcess_nm}},
                                        {"range": {"@upload_date": {
                                            "gte": metadata.asat_dt_dash,
                                            "lte": metadata.asat_dt_dash,
                                            "format": "yyyy-MM-dd"
                                        }}},
                                    ],
                                },
                            },
                        ],
                    },
                },
            )
            print("Delete docs successful with:", rs['deleted'])
