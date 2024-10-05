# SQL Uploader

If you upload your skin using FTP there is a short period of time the server
cannot deliver the page once every archive interval. To prevent this you
could use `rsync`, but unfortunately this is not available with all web
space providers. On the other hand, all of them provide databases. So,
what about uploading the web pages to such a database?

This extension tries to do so. Put it between the skin configuration and the
FTP uploader configuration in `weewx.conf`. It then uploads the web pages
to the database and replaces the original HTML files by PHP files that
query the database for the text of the web page and delivers it to the
user. Links are adjustet automatically.

So no changes to the original skin are required.

The content of the PHP files does not change between consequent report
creation cycles. And the original WeeWX FTP uploader does not upload
files that did not change. So there is always a valid web file on the
server.

## Prerequisites

You need the Python MySQL client module.

## Installation instructions

1) download

   ```
   wget -O weewx-sqlupload.zip https://github.com/roe-dl/weewx-sqlupload/archive/master.zip
   ```

2) run the installer

   WeeWX up to version 4.X

   ```shell
   sudo wee_extension --install weewx-sqlupload.zip
   ```

   WeeWX from version 5.0 on and WeeWX packet installation

   ```shell
   sudo weectl extension install weewx-sqlupload.zip
   ```

   WeeWX from version 5.0 on and WeeWX pip installation into an virtual environment

   ```shell
   source ~/weewx-venv/bin/activate
   weectl extension install weewx-sqlupload.zip
   ```
   
3) edit configuration in `weewx.conf`

   See section "Configuration instructions"

5) restart weewx

   for SysVinit systems:

   ```shell
   sudo /etc/init.d/weewx stop
   sudo /etc/init.d/weewx start
   ```

   for systemd systems:

   ```shell
   sudo systemctl stop weewx
   sudo systemctl start weewx
   ```

## Configuration instructions

You can add the configuration either to `weewx.conf` or `skin.conf`.

In `weewx.conf` it looks like that:
```
...
[StdReport]
    ...
    [[SQLupload]]
        enable = true
        skin = SQLupload
        host = replace_me
        username = replace_me
        password = replace_me
        database_name = replace_me
        table_name = replace_me
        [[[SQLuploadGenerator]]]
            #html_divide_tag = html
            #replace_extension = True
            #write_php = True
            [[[[home-page]]]]
                file = index.html
                #html_divide_tag = html
                #replace_extension = True
                #write_php = True
            [[[[other-page]]]]
                file = subdirectory/file.ext
                #replace_extension = True
                #write_php = True
            ...
    [[FTP]]
        ...
...
```

* `file`: file name and path of the file to upload to the database
* `html_divide_tag`: tag, which surrounds the variable part of the page, for
  example `html` or `body`. If the value is `none`, the whole file is
  uploaded to the database. 
* `replace_extension`: if `true` (which is the default), replace the
  extension of the file by `.php` and adjust the links to this file
  within other files processed by this extension appropriately
* `write_php`: write an PHP file instead of the original file

## How to enable PHP on the web server?

If the file name ends with `.php` PHP is processed automatically. So the
easiest way is to let this extension rename all the non-static `.html` files
to `.php` and replace the internal links as well. This is the default
behavior if you do not set special options.

If you set `replace_extension` to `False` (either globally or by file), the
original file name extension is not changed. In this case you have to enable
PHP within the web server configuration. 

```
RewriteEngine On
RewriteCond %{REQUEST_URI} "=/path/on/server/file.ext"
RewriteRule ".*" "-" [H=application/x-httpd-php]
```

## What finally happens

### HTML files

First the file is divided into two parts at the tag specified by
`html_divide_tag`. Where the inner part was within the outer part, PHP code
is included. The inner part is uploaded to the database. The original file
is replaced by the outer part including the PHP code. 

If `replace_extension` is set, the file name extension is replaced by
`.php` and all internal links are adjusted to the new name.

When the user's browser requests the page, the server processes the PHP code 
and so merges the inner part into the outer part. The browser does not see
anything of that dividing and merging.

### Other files

The file is uploaded to the database. If `write_php` is set (which is the
default), the file is replaced by a file containing PHP code. The FTP
uploader then uploads the replacement file. As the content of that file 
does not change between consequtive report cycles, the FTP uploader
does not upload the file again.

When the user's browser requests the file, the server processes the PHP code, 
queries the database for the original file and delivers it to the browser.
