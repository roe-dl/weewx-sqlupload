#!/bin/bash

cp -p ./test_files/* ./HTML_ROOT
python3 ../bin/user/sqlupload.py
