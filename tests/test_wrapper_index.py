from pathlib import Path

from src.wrapper import Es, Index


def test_get_mapping(es: Es, test_path: Path):
    index: Index = es.index(name='home-product')
    rs = index.get_mapping()
    print(type(rs))
    print(rs)
    print(rs.body)

    test_file: Path = test_path / 'test_mapping_home_product.json'
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


def test_count(es: Es):
    index: Index = es.index(name='home-product')
    rs: int = index.count()
    assert rs >= 0
    assert isinstance(rs, int)
