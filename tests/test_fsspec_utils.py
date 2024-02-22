import os
import pickle

from snapshooter.fsspec_utils import jsonify_file_info, try_find_md5_in_file_info_azure_fs

this_dir = os.path.dirname(os.path.abspath(__file__))
file_info_file = f"{this_dir}/unit_test_data/file_infos_from_azure.pkl"


def test_jsonify_file_info():
    with open(file_info_file, "rb") as f:
        file_infos_from_azure = pickle.load(f)
    jsonified = jsonify_file_info(file_infos_from_azure)
    assert isinstance(jsonified, dict)


def test_jsonify_file_info_unknown_type():
    try:
        jsonify_file_info(object())
        assert False, "Exception expected"
    except Exception as e:
        assert str(e).startswith("Unknown type <class 'object'>")


def test_try_find_md5_in_file_info_azure_fs():
    with open(file_info_file, "rb") as f:
        file_infos_from_azure = pickle.load(f)
    file_infos_by_name = jsonify_file_info(file_infos_from_azure)

    for name, file_info in file_infos_by_name.items():
        md5 = try_find_md5_in_file_info_azure_fs(file_info, {})
        assert isinstance(md5, str)
        assert len(md5) == 32
