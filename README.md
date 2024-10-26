# SQL Upload Generator

* supplement to WeeWX skins to use the database on the web server for
  uninterrupted availability of the web pages
* upload LOOP packets and ARCHIVE records to the database on the web server
  for live update

## Content

* [Why use SQLupload?](#why-use-sqlupload)
* [Prerequisites](#prerequisites)
* [Installation instructions](#installation-instructions)
* [Configuration instructions for skin upload](#configuration-instructions-for-skin-upload)
  * [Simple configuration for use together with the WeeWX built-in Seasons skin](#simple-configuration-for-use-together-with-the-weewx-built-in-seasons-skin)
  * [Configuration preserving the file name extensions](#configuration-preserving-the-file-name-extensions)
* [Configuration instruction for observation data upload](#configuration-instruction-for-observation-data-upload)
* [How to enable PHP on the web server?](#how-to-enable-php-on-the-web-server)
* [Considerations regarding speed](#considerations-regarding-speed)
  * [Upload time vs. file parsing time](#upload-time-vs-file-parsing-time)
  * [Automatic renaming of files vs. special web server setup](#automatic-renaming-of-files-vs-special-web-server-setup)
  * [Overall performance](#overall-performance)
* [What finally happens](#what-finally-happens)
  * [HTML files](#html-files)
  * [JavaScript files](#javascript-files)
  * [Other files](#other-files)
* [Troubleshooting](#troubleshooting)
* [Links](#links)

## Why use SQLupload?

[Zusammenfassung auf Deutsch](https://www.woellsdorf-wetter.de/software/weewx.html#incomplete_page)

You sure experienced this situation: You accessed your weather website,
and you got an incomplete or defective result, or even a 403 or 404
error message. Some seconds later, when you press "reload", all is perfect 
again.

What is the reason for that behavior?

At the end of the archive interval - typically every 5 minutes - WeeWX
newly creates the web pages and uploads them to the web server. While
the file is written, it is incomplete on the server. If you then access
it at the same time, you get a page that looks damaged or no page at
all. This happens more often than you would guess.

To prevent this you could use `rsync`, but unfortunately this is not 
available with all web space providers. On the other hand, all of them 
provide databases. So, what about uploading the web pages using such a 
database?

This extension tries to do so. Put it between the skin configuration and the
FTP uploader configuration in `weewx.conf`. It then uploads the data
to the database and replaces the original files by PHP scripts that
query the database for the content and deliver it to the
user. Links are adjusted automatically.

So generally no changes to the original skin are required. It's simple. See 
[here](#simple-configuration-for-use-together-with-the-built-in-seasons-skin)
how to use it for the WeeWX built-in **Seasons skin**.

The content of the PHP files does not change between consecutive report
creation cycles. And the original WeeWX FTP uploader does not upload
files that did not change. So there is always a valid web file on the
server now with no interruption any more.

And finally, if you cannot use MQTT for whatever reason but want to
show live data, uploading the LOOP packets and ARCHIVE records by SQL to 
the database on the web server could be an alternative approach.

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

   See section [Configuration instructions](#configuration-instructions)

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

## Configuration instructions for skin upload

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
            #actions = sqlupload, writephp, adjustlinks
            #html_divide_tag = html
            #preserve_file_name_extension = false
            #replace_links_to_this_file = true
            #merge_skin = replace_me
            [[[[home-page]]]]
                file = index.html
                #actions = sqlupload, writephp, adjustlinks
                #html_divide_tag = html
                #preserve_file_name_extension = false
                #replace_links_to_this_file = true
            [[[[other-page]]]]
                file = subdirectory/file.ext
                #actions = sqlupload, writephp, adjustlinks
                #preserve_file_name_extension = false
                #replace_links_to_this_file = true
            ...
    [[FTP]]
        ...
...
```

Options for general use:
* `merge_skin`: If this key points to a valid skin, its `skin.conf` file is
  searched for templates, and SQLupload entries are created for each of them
  and merged into the configuration. If a section of the same name exists
  in both the skin and the SQLupload configuration, the SQLupload section 
  takes precedence over the skin section.
  The sections `[CheetahGenerator][[ToDate]]` and `[ImageGenerator]` are
  searched only. Entries that contain one of the keys `generate_once` or
  `stale_age` are not included.
* `enable`: enable this entry or not, optional, default `True`.
  If you overwrite an entry out of the skin included by `merge_skin`, you
  can use the option `enable` to exclude a file from being processed
  by SQLupload.
* `file`: file name and path of the file to upload to the database
* `actions`: a comma-separated list of actions to perform:
  * `sqlupload`: upload the file to the database. In case of HTML divide
    it into a constant and a variable part as defined by the 
    `html_divide_tag` key and upload the variable part only.
  * `writephp`: replace the original file by a PHP script. In case of HTML,
    this PHP script includes the constant part. The PHP is then uploaded
    by FTP together with the other constant files of the skin. Its 
    purpose is to query the database and to deliver the content of the
    file it replaced.
  * `adjustlinks`: adjust the links to other files to reflect the change
    of their file name extension. Effective only for HTML and JavaScript
    files. This can be the only option (or combined with `noremove`) in 
    case of static files that are to upload by FTP later.
  * `blockftp`: update the FTP upload generator state file in order
    to prevent the file from being uploaded by both SQL and FTP.
    Alternative to `remove`. This is the default.
  * `remove`: remove the original file after SQL upload in order to prevent
    it from being uploaded by both SQL and FTP. Alternative to `blockftp`.
  The default is `sqlupload, writephp, blockftp, adjustlinks` if omitted.
* `html_divide_tag`: tag, which surrounds the variable part of the page, for
  example `html` or `body`. If the value is `none`, the whole file is
  uploaded to the database. Effective only for HTML files.
* `preserve_file_name_extension`: preserve the original file name extension 
  while writing the PHP script. Together with action `writephp` only. 
  If you use this option you need special settings within the web server
  configuration to enable PHP for file name extensions other than
  `.php`.
* `replace_links_to_this_file`: if true (which is the default) adjust all 
  the links to this file within other files 
* `first_run_only`: process this entry in the first run after the WeeWX
  start only, optional, default is to process it every report creation
  cycle. Use this option for files that are listed in the value of the
  `copy_once` key of the skin and contain links or references to targets 
  that are subject to the `writephp` action.

Options in case of trouble:
* `php_mysql_driver`: PHP MySQL driver to use, either `pdo` or `mysqli`,
  optional, default `pdo` if omitted
* `sql_data_type`: data type of the database column that holds the 
  web page data, optional, default `LONGBLOB`
* `file_uploader`: section within `[StdReport]` that holds the related
  FTP uploader configuration, optional, default `FTP`.

### Simple configuration for use together with the WeeWX built-in Seasons skin

1. Activate the database at you web spcace
2. Insert the SQLupload configuration to `weewx.conf`

   ```
   ...
   [StdReport]
       ...
       [[SeasonsReport]]
           ...
       ...
       [[SQLupload]]
           enable = true
           skin = SQLupload
           host = replace_me
           username = replace_me
           password = replace_me
           database_name = replace_me
           table_name = seasons
           [[[SQLuploadGenerator]]]
               merge_skin = SeasonsReport
               [[[[seasons.js]]]]
                   actions = adjustlinks
                   first_run_only = true
       [[FTP]]
           ...
   ```

3. Restart WeeWX as described above
4. Remove `.html` and `.png` files from both the `HTML_ROOT` directory and
   the web space
5. Wait for the next report creation cycle to take place
6. If you then enter the URL of your web server into the browser, the web
   server delivers `index.php` instead of former `index.html` automatically
   and displays the skin as before.

### Configuration preserving the file name extensions

If you have a website that is known to search engines like Google for a
long time already, you would want to preserve the file names for SEO
reasons. To do so you can set up special rules in `.htaccess` on the
web server. The Belchertown skin is used here as an example how the
configuration looks like for this case.

in `weewx.conf`:
```
...
[StdReport]
    ...
    [[Belchertown]]
        ...
    ...
    [[SQLupload]]
        enable = true
        skin = SQLupload
        host = replace_me
        username = replace_me
        password = replace_me
        database_name = replace_me
        table_name = belchertown
        [[[SQLuploadGenerator]]]
            actions = sqlupload, writephp
            merge_skin = Belchertown
    [[FTP]]
        ...
```

Please note that SQLupload additionally reads the Belchertown specific
`graphs.conf` file for chart definition files if
`user.belchertown.HighchartsJsonGenerator` is in `generator_list` of
`skin.conf`.

in `.htaccess`:
```
RewriteCond %{REQUEST_URI} "=/json/weewx_data.json"
RewriteRule "^(.*).json$" "$1.php" [L]
RewriteCond %{REQUEST_URI} "=/js/belchertown.js"
RewriteRule "^(.*).js$" "$1.php" [L]
RewriteCond %{REQUEST_URI} "=/manifest.json"
RewriteRule "^(.*).json$" "$1.php" [L]
RewriteCond %{REQUEST_URI} "=/json/homepage.json"
RewriteRule "^(.*).json$" "$1.php" [L]
RewriteCond %{REQUEST_URI} "=/json/day.json"
RewriteRule "^(.*).json$" "$1.php" [L]
RewriteCond %{REQUEST_URI} "=/json/week.json"
RewriteRule "^(.*).json$" "$1.php" [L]
RewriteCond %{REQUEST_URI} "=/json/month.json"
RewriteRule "^(.*).json$" "$1.php" [L]
RewriteCond %{REQUEST_URI} "=/json/year.json"
RewriteRule "^(.*).json$" "$1.php" [L]
RewriteCond "%{DOCUMENT_ROOT}$1.php" -f
RewriteRule "^(.*).html$"      "$1.php"
```

This way all the pages are available under the names they were known before.
Of course, if you do not care about SEO you can remove the `actions` line 
from the configuration and access the pages using the file name extension
`.php`.

## Configuration instruction for observation data upload

Put the configuration into `weewx.conf`.

```
[StdRESTful]
    ...
    [[SQLupload]]
        enable = true
        binding = LOOP, ARCHIVE
        unit_system = METRIC
        host = replace_me
        username = replace_me
        password = replace_me
        database_name = replace_me
        table_name = replace_me
        log_success = false
...
[Engine]
    [[Services]]
        ...
        restful_services = ..., user.sqlupload.SQLRESTful
```

You can use the same table for both skin upload and observation data upload.

If you upload LOOP packets we strongly recommend to switch off logging
successful uploads. 

The options are:
* `binding`: whether to upload LOOP packets or ARCHIVE records or both
* `unit_system`: unit system to use for upload. Possible values are
  `US`, `METRIC`, or `METRICWX`.
* `host`: name of the database server
* `username`: user name for the database server
* `password`: password for the database server
* `database_name`: name of the database on the server
* `table_name`: name of the table to write data to

The record ID for the LOOP packets is `LOOP` and for the ARCHIVE records
`ARCHIVE`. Both records contain data in JSON format. To process them on
the server, you can use PHP, similar to this example:

```php
<?php
  $id = "LOOP";
  $dbhost = "localhost";
  $dbuser = "replace_me";
  $dbpassword = "replace_me";
  $dbname = "replace_me";
  $pdo = new PDO(
    "mysql:host=localhost;dbname=$dbname",
    $dbuser,
    $dbpassword
  );
  $sql = "SELECT *,UNIX_TIMESTAMP(`MTIME`) AS MTIME_EPOCH FROM belchertown WHERE `ID`=?";
  $statement = $pdo->prepare($sql); 
  $statement->execute([$id]);
  $text = "";
  while($row = $statement->fetch()) {
    $text = $text . $row["TEXT"];
    header("Last-Modified: " . date("r", $row["MTIME_EPOCH"]));
    header("Content-Type: " . $row["CONTENTTYPE"]);
  }
  echo $text;
  $pdo = null;
?>
```

If you save that script on your web server and open it with your browser,
you will see the actual observation data. You can then use JavaScript to
process it further. Replace `LOOP` with `ARCHIVE` for the last archive record.

Instead of sending data to the browser as is (like in the example above),
you can process it within the PHP script, too, and then deliver to the
browser whatever you made out of the observation data.

## How to enable PHP on the web server?

This is not about configuring PHP or web servers in general. This is
about the special requirements of this extension only.

### File name extension `.php`

If the file name ends with `.php` the server processes PHP automatically. 
So the easiest way is to let this extension rename all the non-static 
`.html` files to `.php` and replace the internal links as well. This is 
the default behavior if you do not set special options.

If you want to reference the file or page by the original name irrespective
of the name change, you can set up a re-write rule in `.htacces`. It could
look like that:

```
RewriteCond %{REQUEST_URI} "=/path/on/server/file.ext"
RewriteRule "^(.*).html$" "$1.php" [L]
```

Or to generally deliver the `.php` files if `.html` is requested:

```
RewriteCond   "%{DOCUMENT_ROOT}$1.php"    -f
RewriteCond   "%{DOCUMENT_ROOT}$1.html"   !-f
RewriteRule   "^(.*).html$"               "$1.php"
```

Source: [Redirecting and Remapping with mod_rewrite, Backward Compatibility](https://httpd.apache.org/docs/trunk/rewrite/remapping.html#backward-compatibility)

### Preserving the original file name extension

If you set `preserve_file_name_extension` to `true` (either globally or by 
file), the original file name extension is not changed. In this case you have 
to enable PHP within the web server configuration. 

You could do so by putting this into `.htaccess`:
```
RewriteEngine On
RewriteCond %{REQUEST_URI} "=/path/on/server/file.ext"
RewriteRule ".*" "-" [H=application/x-httpd-php]
```

This may work or not, depending on the general configuration of the web
server. To check you can save the following code as `test.html` and
`test.php` to your web space and compare the result:

```php
<?php
  phpinfo();
?>
```

If you open those files in your web server you see a long table of
configuration data. If you see the text above only, PHP is not enabled. If
you get a different output from `test.html` and `test.php` the web server is
probably not configured to process PHP within files with file name extension
`.html`. You may ask your web space provider to adjust the configuration.
Especially look at the item "Additional .ini files parsed".

## Considerations regarding speed

### Upload time vs. file parsing time

Speed may be an issue. There are two things that may take time:

* uploading, especially with a slow Internet connection
* parsing the HTML and JavaScript files for links and dividing them into
  a constant and a variable part

There are areas around the world where Internet speed is considerably poor. 
Think of rural regions of Germany for example. There it can help to reduce
the amount of data to transfer. You can do so by dividing HTML files into
a constant part (for example the header) and a variable part (then the 
body). The constant part is then transfered only once, while the variable
part is transfered every archive interval. 

But there is also a disadvantage. Parsing the HTML file to find the 
position to divide it takes time, too. So you have to compare which of
them takes more time. 

### Automatic renaming of files vs. special web server setup

Another thing that influences speed is the link adjustment. Again there
are two possibilities:

* Let SQLupload parse all HTML and JavaScript files and adjust the
  file name extension to `.php`. So you need no special web server
  configuration.
* Do not change links and set up a `.htaccess` file to map the old
  file names to the PHP file names. So you need not parse the HTML
  and JavaScript files.

If you already parse the HTML files in order to divide them, there is no
additional costs of time for adjusting the links. So in this case it
does not matter which possibility you use here.

You can set `profiling` to `2` in `skin.conf` to see how much CPU time 
each of the files consumes for parsing. 

### Overall performance

At the author's system the upload time was cut to approximately to half
by using SQLupload compared to pure FTP. I am not sure about the general
performance of SQL compared to FTP.

## What finally happens

### HTML files

First the file is divided into two parts at the tag specified by
`html_divide_tag` (except it is set to `none`). The inner part is extracted 
and uploaded to the database. Where the inner part was within the outer part, 
PHP code to query the database is included. The original file is replaced 
with the outer part with the PHP code inserted. 

If `preserve_file_name_extension` is not set, the file name extension is 
replaced with `.php` and if `replace_links_to_this_file` is set, 
all internal links are adjusted to the new name.

When the user's browser requests the page, the server processes the PHP code 
and so merges the inner part into the outer part. The browser does not see
anything of that dividing and merging.

### JavaScript files

JavaScript code can contain references to other files. So the file is
searched for such references, and they are adjusted appropriately.
In a typical configuration the file is changed in place and uploaded
by FTP later. No database upload is performed for those files. It
is not necessary because they do not change regularly.

The Belchertown skin uses a special regular expression to get
the relative paths of its web pages. This WeeWX extension knows about
it and adjusts it to make it working with `.php` files, too.

### Other files

The file is uploaded to the database. If `writephp` is set (which is the
default), the file is replaced by a file containing PHP code to query the
database. The FTP uploader then uploads the replacement file. As the content 
of that file does not change between consecutive report cycles, the FTP 
uploader does not upload the file again.

When the user's browser requests the file, the server processes the PHP code, 
queries the database for the original file and delivers it to the browser.

## Troubleshooting

* See the syslog for messages containing `user.sqlupload`. They may reveal 
  some reason for problems.
* To get more details in logging set `debug = 1` in `weewx.conf` and restart
  WeeWX
* Don't forget to remove all the files that are uploaded before setting up
  the SQLupload extension at both the `HTML_ROOT` directory and the
  web space.
* Make sure the SQLupload configuration section is placed before the FTP
  section but after the skin section in `weewx.conf`.
* Make sure `HTML_ROOT` points to the same directory in the skin
  configuration, the SQLupload configuration, and the FTP upload 
  configuration.
* Your web space provider may require you to create the database at their
  administration portal.
* There are different PHP MySQL drivers. If you encounter error messages
  about unsuccessful database access in the browser, try another driver
  by setting `php_mysql_driver` to `mysqli` or `pdo`, respectively.
* If you are not sure whether the database records are updated, open an
  SQL editor and open the table. There is a column holding the 
  modification time included.

## Links

* [Apache Module mod_rewrite](https://httpd.apache.org/docs/2.4/mod/mod_rewrite.html)
* [Apache Redirecting and Remapping with mod_rewrite](https://httpd.apache.org/docs/trunk/rewrite/remapping.html)
* [weewx-user: Page not found or empty](https://groups.google.com/g/weewx-user/c/Ioykua7OJm0/m/EYtd_UTMAwAJ)
* [weewx-user: FTP upload and web page display errors](https://groups.google.com/g/weewx-user/c/TLard6hoKxw)
* [SFTP uploader weewx-sftp](https://github.com/matthewwall/weewx-sftp)
* [WeeWX](https://weewx.com)
