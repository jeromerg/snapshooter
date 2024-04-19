import re

re_numbers = re.compile('([0-9]+)')

def natural_sort_key(s):
    return [
        int(text) if text.isdigit() else text
        for text in re_numbers.split(s)
    ]