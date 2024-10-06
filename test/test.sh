#!/bin/bash

cp ./test_files/* ./HTML_ROOT
python3 ../bin/user/sqlupload.py
