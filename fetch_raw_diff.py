import os
import re
import requests
from requests.adapters import HTTPAdapter
# from bs4 import BeautifulSoup
# from util import language_tool

def parse_diff(file_name, diff):
    parts = re.split('(@@.*?-.*?\+.*?@@)', diff)
    start_with_plus_regex = re.compile('^\++')
    start_with_minus_regex = re.compile('^\-+')

    add_diff_code = ""
    del_diff_code = ""
    add_code_line = 0
    del_code_line = 0
    add_location_set = []
    del_location_set = []

    parts_len = len(parts)
    for i in range(1, parts_len, 2):
        location = parts[i]
        part = parts[i + 1]
        
        try:
            add, dele = location.replace('@','').strip().split(' ')
        except:
            continue
        
        if len(part) >= 100 * 1024:
            continue
        
        try:
            if ('-' in add) and ('+' in dele):
                add, dele = dele, add

            if ',' in add:
                add_location, add_line = add[1:].split(',')
            else:
                add_location = 0
                add_line = add[1:]

            if ',' in dele:
                del_location, del_line = dele[1:].split(',')
            else:
                del_location = 0
                del_line = dele[1:]

            lines_of_code = [x.strip() for x in part.splitlines()]

            added_lines_of_code = filter(lambda x: (x) and (x[0] == '+'), lines_of_code)
            added_lines_of_code = [start_with_plus_regex.sub('', x) for x in added_lines_of_code]

            deleted_lines_of_code = filter(lambda x: (x) and (x[0] == '-'), lines_of_code)
            deleted_lines_of_code = [start_with_minus_regex.sub('', x) for x in deleted_lines_of_code]

            add_diff_code += '\n'.join(added_lines_of_code) + '\n'
            del_diff_code += '\n'.join(deleted_lines_of_code) + '\n'

            add_code_line += len(added_lines_of_code)
            del_code_line += len(deleted_lines_of_code)

            add_location_set.append([int(add_location), int(add_line)])
            del_location_set.append([int(del_location), int(del_line)])
        except Exception as e:
            print('Parse Error:', e)
    
    return {"name": file_name, 
            "LOC": {
                "add": add_code_line,
                "del": del_code_line,
             },
            "location":{
                "add": add_location_set,
                "del": del_location_set,
             },
            "add_code": add_diff_code,
            "del_code": del_diff_code,
           }

def parse_files(r):
    file_list = []
    diff_list = r.split('diff --git')
    for diff in diff_list[1:]:
        try:
            file_full_name = re.findall('a\/.*? b\/(.*?)\n', diff)[0]
        except:
            continue
        file_list.append(parse_diff(file_full_name, diff))
    return file_list

def fetch_raw_diff(url):
    s = requests.Session()
    s.mount('https://github.com', HTTPAdapter(max_retries=3))

    try:
        r = s.get(url, timeout=120)
        if r.status_code != requests.codes.ok:
            raise Exception('error on fetch compare page on %s!' % url)
    except:
        raise Exception('error on fetch compare page on %s!' % url)

    return parse_files(r.text)

if __name__ == '__main__':
    # print(fetch_raw_diff('https://github.com/MarlinFirmware/Marlin/commit/6b43bfa01dd76f5475acf40d0e5b5f240fe57d9e'))
    # print([x["location"] for x in fetch_raw_diff('https://github.com/mozilla-b2g/gaia/pull/34385.diff')])
    # print(fetch_raw_diff('https://github.com/mozilla-b2g/gaia/pull/34384.diff'))
    # print([x["location"] for x in fetch_raw_diff('https://patch-diff.githubusercontent.com/raw/moby/moby/pull/21495.diff')])
    # print(fetch_raw_diff("https://patch-diff.githubusercontent.com/raw/FancyCoder0/INFOX/pull/146.diff"))
    
    # fetch_raw_diff('https://github.com/kubernetes/kubernetes/pull/55744.diff')
    print('ok')