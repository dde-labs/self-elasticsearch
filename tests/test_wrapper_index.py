from pathlib import Path
from pprint import pprint
from typing import Any

from src.wrapper import Es, Index


def test_get_mapping(es: Es, test_path: Path):
    index: Index = es.index(name='home-store')
    rs = index.get_mapping()
    print(type(rs))
    print(rs)
    print(rs.body)

    test_file: Path = test_path / 'test-mapping-home-store.json'
    index.get_mapping(output=test_file)

    # test_file.unlink(missing_ok=True)


def test_get_setting(es: Es, test_path: Path):
    index: Index = es.index(name='home-product')
    rs = index.get_setting()
    print(type(rs))
    print(rs)
    print(rs.body)

    test_file: Path = test_path / 'test_setting_home_product.json'
    index.get_setting(output=test_file)

    # test_file.unlink(missing_ok=True)


def test_create_index(es: Es):
    index: Index = es.index(name='tmp-korawica-home-product')
    assert not index.exists

    rs = index.create(
        setting={
            "number_of_shards": 1,
            "number_of_replicas": 0
        },
        mapping={
            "properties": {
                "barcode": {"type": "keyword"},
                "brand": {"type": "text", "analyzer": "icu_analyzer"},
                "cms_id": {"type": "keyword"},
                "height_number": {"type": "float"},
                "article_id": {"type": "integer"},
                "upload_date": {"type": "date"},
            }
        }
    )
    assert rs.body == {
        'acknowledged': True,
        'shards_acknowledged': True,
        'index': 'tmp-korawica-home-product',
    }


def test_count(es: Es):
    index: Index = es.index(name='home-product')
    rs: int = index.count()
    assert rs >= 0
    assert isinstance(rs, int)


def test_truncate(es: Es):
    index: Index = es.index('tmp-korawica-home-product')
    rs = index.truncate(auto_refresh=True)
    print(type(rs))
    print(rs)


def test_search_by_query(es: Es):
    index: Index = es.index(name='home-product')
    rs = index.search_by_query(
        query={"bool": {"filter": {"term": {"cms_id": "307720"}}}}
    )
    hits: list[Any] = rs.body['hits']['hits']
    for hit in hits:
        body = {
            k: hit['_source'][k]
            for k in hit['_source']
            if (
                any(
                    k.startswith(_)
                    for _ in ('weight', 'width', 'length', 'height')
                )
            )
        }
        pprint(body, indent=2)
        print('-' * 100)


def test_search_by_query_limit(es: Es):
    index: Index = es.index(name='home-store')
    rs = index.search_by_query(
        query={
            "bool": {"must": [{"match_all": {}}], "must_not": [], "should": []}
        },
        size=1,
    )
    hits: list[Any] = rs.body['hits']['hits']
    print(hits)


def test_delete_by_query(es: Es):
    index: Index = es.index(name='home-product')
    rs = index.search_by_query(query={"match": {"barcode": "8852402136755"}})
    total: dict = rs['hits']['total']

    if total['value'] == 1:
        rs = index.delete_by_query(query={"match": {"barcode": "8852402136755"}})
        assert rs['deleted'] == 1
