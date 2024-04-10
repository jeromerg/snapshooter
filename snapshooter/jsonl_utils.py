import json


def loads_jsonl(content:str):
    result = []
    for idx, line in enumerate(content.split("\n")):
        if line.strip() == "":
            continue
        try:
            line_obj = json.loads(line)
        except Exception as e:
            raise ValueError(f"Error parsing line {idx}: {line}") from e
        result.append(line_obj)
    return result


def dumps_jsonl(items:list):
    return "\n".join(_dumps_jsonl_rows(items))


def _dumps_jsonl_rows(items:list):
    for idx, item in enumerate(items):
        try:
            line = json.dumps(item, sort_keys=True)
        except Exception as e:
            raise ValueError(f"Error dumping line {idx}: {item}") from e
        yield line
