# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import os
import requests
import time
import argparse

def download_files(file_list, download_dir):
    base = "https://vcdpublic.blob.core.windows.net/vcd1"
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    with open(file_list, 'r') as f:
        lines = f.readlines()
        for line in lines:
            file_path = line.strip()            
            url = base + '/' +file_path
            filename = url.split('/')[-1]
            print('Downloading', filename)
            # make sure base[:-1] path exists            
            os.makedirs(os.path.join(download_dir, "/".join(file_path.split('/')[:-1]) ), exist_ok=True)
            r = requests.get(url, stream=True)
            with open(os.path.join(download_dir, file_path), 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
            time.sleep(1)

    print('Download complete')

if __name__ == '__main__':
    
    parser = argparse.ArgumentParser(description='Utility script to evaluate answers to the pcrowd batch')
    # Configuration: read it from mturk.cfg
    parser.add_argument("--list-of-files", required=True,
                        help="List of files to download")
    parser.add_argument("--local-path", required=True,
                        help="Local path to download the files to")
    args = parser.parse_args()

    download_files(args.list_of_files, args.local_path)
