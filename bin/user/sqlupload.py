#!/usr/bin/python3
# WeeWX generator to upload skins using a database
# Copyright (C) 2024 Johanna Roedenbeck

"""

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""

"""
    This uploader fits in between report creation and FTP upload. It divides
    the files into a constant and a variable part if possible and then
    uploads the variable part (or the whole file, if there is no constant
    part or it cannot be determined) to the web server by SQL. After that
    it replaces the files by PHP scripts that are to fetch the data from
    the database.
    
    Q: Why the files are replaced instead of saving them to another place?
    
    A: The report creation also puts files into the directory that are not 
       subject to processing by this uploader. The FTP uploader has to upload 
       them as well as the PHP scripts. So they need to reside in the same
       directory.
"""

VERSION = "0.3"

import os
import os.path
import configobj
import time
import html.parser
import json

try:
    # Python 3
    import queue
except ImportError:
    # Python 2
    # noinspection PyUnresolvedReferences
    import Queue as queue

try:
    import hashlib
    has_hashlib = True
except ImportError:
    has_hashlib = False

if __name__ == '__main__':
    import sys
    sys.path.append('/usr/share/weewx')

try:
    try:
        from six.moves import cPickle as pickle
    except ImportError:
        import pickle
    import weeutil.ftpupload
    has_pickle = True
except ImportError:
    has_pickle = False

import weewx
import weewx.reportengine
import weewx.restx
import weewx.manager
import weeutil.weeutil
import weeutil.config
import weedb.mysql

if __name__ == '__main__':
    def logdbg(msg):
        print('DEBUG',msg)
    def loginf(msg):
        print('INFO',msg)
    def logerr(msg):
        print('ERROR',msg)
else:
    import weeutil.logger
    import logging
    log = logging.getLogger("user.sqlupload")
    def logdbg(msg):
        log.debug(msg)
    def loginf(msg):
        log.info(msg)
    def logerr(msg):
        log.error(msg)

def get_php_filename(file):
    """ get the file name for the PHP script out of the orignal file name
    
        Args:
            file (str): file name of the file to upload
        
        Returns:
            str: file name for the PHP script
    """
    return '%s.php' % (os.path.splitext(file)[0] if file.endswith('.html') or file.endswith('.htm') else file)

class HTMLdivide(html.parser.HTMLParser):
    """ divide an HTML file into a constant and a variable part and replace URLs
    
        Args:
            php (str): PHP script to insert into the constant part where the
                variable part was extracted
            files_list (list): list of URLs to replace
            divide_tag (str): tag which divides the constant part from the
                variable one (use `none` to have no constant part)
        
        Returns:
            php_data (str): constant part including PHP to upload as a file
            db_data (str): variable part to upload by SQL
    """

    def __init__(self, php, files_list, divide_tag='html', convert_charrefs=True):
        super(HTMLdivide,self).__init__(convert_charrefs=convert_charrefs)
        self.php_data = ''
        self.db_data = ''
        self.inner = divide_tag=='none'
        self.divide_tag = divide_tag
        self.php_script = php
        self.files = files_list

    def handle_starttag(self, tag, attrs):
        if tag=='a':
            # replace href to HTML by PHP
            for idx, val in enumerate(attrs):
                if val[0]=='href':
                    separator = '?' if '?' in val[1] else '#'
                    href = val[1].split(separator)
                    if self.isinfiles(href[0]):
                        href[0] = get_php_filename(href[0])
                        attrs[idx] = ('href',separator.join(href))
        s = '<%s %s>' % (tag,' '.join('%s="%s"' % i for i in attrs))
        if self.inner:
            self.db_data += s
        else:
            self.php_data += s
        if tag==self.divide_tag:
            self.inner = True
            self.php_data += '\n%s\n' % self.php_script
    
    def handle_endtag(self, tag):
        if tag==self.divide_tag: self.inner = False
        s = '</%s>' % tag
        if self.inner:
            self.db_data += s
        else:
            self.php_data += s
    
    def handle_data(self, data):
        if self.inner:
            self.db_data += data
        else:
            self.php_data += data
    
    def handle_startendtag(self, tag, attrs):
        if True:
            for idx, val in enumerate(attrs):
                if val[0]=='src':
                    href = val[1].split('?')
                    if self.isinfiles(href[0]):
                        href[0] = get_php_filename(href[0])
                        attrs[idx] = ('src','?'.join(href))
        s = '<%s %s />' % (tag,' '.join('%s="%s"' % i for i in attrs))
        if self.inner:
            self.db_data += s
        else:
            self.php_data += s
    
    def handle_comment(self, data):
        s = '<!-- %s -->' % data
        if self.inner:
            self.db_data += s
        else:
            self.php_data += s
    
    def handle_decl(self, decl):
        self.php_data += '<!%s>' % decl
    
    def handle_entityref(self, name):
        s = '&%s;' % name
        if self.inner:
            self.db_data += s
        else:
            self.php_data += s
    
    def handle_charref(self, name):
        s = '&#%s;' % name
        if self.inner:
            self.db_data += s
        else:
            self.php_data += s
    
    def isinfiles(self, href):
        if not href: return False
        if href.startswith('http'): return False
        if href.startswith('../'):
            x = href[3:]
        elif href.startswith('./'):
            x = href[2:]
        else:
            x = href
        return x in self.files


if __name__ == '__main__':
    class ConnTest(object):
        """ print SQL statements for dry run """
        def begin(self):
            print('SQL begin')
        def commit(self):
            print('SQL commit')
        def execute(self, sql, attrs=()):
            print('SQL execute %s %s' % (sql,attrs))
        def close(self):
            print('SQL close')


class SQLuploadGenerator(weewx.reportengine.ReportGenerator):
    """ SQL upload generator """

    PHP_START = '<?php\n'
    PHP_END = '?>'
    PHP_INCL = '  $id="%s";\n  include "%s";\n'
    PHP_PDO = '''  $pdo = new PDO(
    "mysql:host=localhost;dbname=$dbname",
    $dbuser,
    $dbpassword
  );
  $sql = "SELECT %s FROM %s WHERE `ID`=?";
  $statement = $pdo->prepare($sql); 
  $statement->execute([$id]);
  $text = "";
  while($row = $statement->fetch()) {
    $text = $text . $row["TEXT"];
    header("Last-Modified: " . date("r", $row["MTIME_EPOCH"]));
    header("Content-Type: " . $row["CONTENTTYPE"]);
  }
  $pdo = null;
'''
    PHP_MYSQLI = '''  $pdo = new mysqli("localhost",$dbuser,$dbpassword,$dbname);
  $sql = "SELECT %s FROM %s WHERE `ID`='" . $id . "'";
  $reply = $pdo->query($sql);
  $text = "";
  while($row = $reply->fetch_assoc()) {
    $text = $text . $row["TEXT"];
    header("Last-Modified: " . date("r", $row["MTIME_EPOCH"]));
    header("Content-Type: " . $row["CONTENTTYPE"]);
  }
  $pdo->close();'''
    PHP_INI = '''  $dbhost = "%s";
  $dbuser = "%s";
  $dbpassword = "%s";
  $dbname = "%s";
'''
    PHP_ECHO = '  echo $text;\n'
    
    # SQL commands
    SQL_UPDATE = 'UPDATE %s SET `TEXT`=?,`CONTENTTYPE`=?,`MTIME`=FROM_UNIXTIME(?) WHERE `ID`=?'
    SQL_INSERT = 'INSERT IGNORE INTO %s(`ID`) VALUES (?)'
    SQL_CREATE = 'CREATE TABLE IF NOT EXISTS %s(`ID` CHAR(32) PRIMARY KEY, `MTIME` TIMESTAMP NULL DEFAULT NULL, `CONTENTTYPE` VARCHAR(127) NULL, `TEXT` %s NULL)'
    SQL_SELCOL = '*,UNIX_TIMESTAMP(`MTIME`) AS MTIME_EPOCH'

    # files to process by `process_other()` and their MIME types
    # Note: HTML and JavaScript must not be included here.
    OTHER_FILES = {
        '.css':  'text/css',
        '.txt':  'text/plain',
        '.json': 'application/json',
        '.xml':  'application/xml',
        '.pdf':  'application/pdf',
        '.png':  'image/png',
        '.jpg':  'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.svg':  'image/svg+xml',
        '.gif':  'image/gif',
        '.bmp':  'image/bmp',
        '.webp': 'image/webp',
        '.mp3':  'audio/mpeg',
        '.mpeg': 'audio/mpeg',
        '.mp4':  'video/mp4',
    }
    
    def __init__(self, config_dict, skin_dict, gen_ts, first_run, stn_info, record=None):
        super(SQLuploadGenerator,self).__init__(config_dict, skin_dict, gen_ts, first_run, stn_info, record)
        self.running = True
        self.phpuser = ('weewxphpuser','Wcw4nNiQHvvNVAwzFogj')
        if first_run:
            loginf("Report skin name '%s', skin version '%s'" % (
                     skin_dict.get('SKIN_NAME'),skin_dict.get('SKIN_VERSION')))

    def shutDown(self):
        self.running = False
        loginf('request to shutdown SQLuploadGenerator')

    def run(self):
    
        # determine how much logging is desired
        log_success = weeutil.weeutil.to_bool(weeutil.config.search_up(self.skin_dict, 'log_success', True))
        log_failure = weeutil.weeutil.to_bool(weeutil.config.search_up(self.skin_dict, 'log_failure', True))

        # where to find the files
        if self.skin_dict['HTML_ROOT'].startswith('~'):
            target_path = os.path.expanduser(self.skin_dict['HTML_ROOT'])
        else:
            target_path = os.path.join(
                self.config_dict['WEEWX_ROOT'],
                self.skin_dict['HTML_ROOT'])

        # configuration section for this generator
        generator_dict = self.skin_dict.get('SQLuploadGenerator',
                                                         configobj.ConfigObj())
        self.dry_run = generator_dict.get('dry_run',False)
        if 'merge_skin' in generator_dict:
            self.merge_skin(generator_dict)
        if __name__ == '__main__':
            print('---- generator_dict ----')
            print(json.dumps(generator_dict,indent=4,ensure_ascii=False))
            print('------------------------')
        else:
            with open(os.path.join(target_path,'#SQLupload.conf'),'wt') as f:
                f.write(json.dumps(generator_dict,indent=4,ensure_ascii=False))
        
        # database 
        dbhost = self.skin_dict.get('host')
        dbport = weeutil.weeutil.to_int(self.skin_dict.get('port',3306))
        dbname = self.skin_dict.get('database_name')
        username = self.skin_dict.get('username')
        password = self.skin_dict.get('password')
        tablename = generator_dict.get('table_name',
                                              self.skin_dict.get('table_name'))
        phpdriver = self.skin_dict.get('php_mysql_driver','PDO').lower()
        blobtype = self.skin_dict.get('sql_data_type','LONGBLOB')
        sqlcolumns = SQLuploadGenerator.SQL_SELCOL
        sql_upd_str = SQLuploadGenerator.SQL_UPDATE % tablename
        sql_ins_str = SQLuploadGenerator.SQL_INSERT % tablename
        
        # related FTP upload section
        ftp_uploader_section = self.skin_dict.get('file_uploader','FTP')
        ftp_uploader_dict = self.config_dict.get('StdReport',configobj.ConfigObj()).get(ftp_uploader_section,configobj.ConfigObj())
        ftp_target_path = weeutil.config.search_up(ftp_uploader_dict,'HTML_ROOT',target_path)
        if ftp_target_path.startswith('~'):
            ftp_target_path = os.path.expanduser(ftp_target_path)
        else:
            ftp_target_path = os.path.join(
                self.config_dict['WEEWX_ROOT'],
                ftp_target_path
            )
        logdbg("FTP uploader HTML_ROOT=%s" % ftp_target_path)
        if dbhost!=ftp_uploader_dict.get('server'):
            loginf(
                "Warning! Different servers. SQL --> '%s', FTP --> '%s'" % (
                                       dbhost,ftp_uploader_dict.get('server'))
            )
        
        # archive interval
        """
        try:
            archive_interval = self.record['interval']*60
        except (TypeError,LookupError):
            archive_interval = config_dict.get('StdArchive',
                             configobj.ConfigObj()).get('archive_interval',300)
        interval_start = self.gen_ts
        interval_end = self.gen_ts+archive_interval
        """
        
        is_new_database = None
        
        start_ts = time.time()
        
        # Hashes of the data uploaded during the last run
        sql_last_upload = SQLlastUpload(target_path)
        ftp_last_upload = FTPlastUpload(ftp_target_path)
        
        if self.dry_run:
            conn = ConnTest()
        else:
            # try to create database at first run after start of WeeWX
            if self.first_run:
                try:
                    weedb.mysql.create(
                        host=dbhost,
                        user=username,
                        password=password,
                        database_name=dbname,
                        port = dbport
                    )
                except weedb.DatabaseExistsError:
                    is_new_database = False
                except Exception as e:
                    if log_failure:
                        logerr('creating database failed: %s %s' % (e.__class__.__name__,e))
                    return
                else:
                    if log_success:
                        loginf("successfully created database '%s' on '%s'" % (dbname,dbhost))
                    is_new_database = True

            # connect to the database
            conn = weedb.mysql.connect(
                host=dbhost,
                user=username,
                password=password,
                database_name=dbname,
                port=dbport
            )
            if not conn:
                if log_failure:
                    logerr('could not connect to database')
                    return
            if is_new_database:
                self.create_user(conn, dbname, tablename)
        
        # try to create table at first run after the start of WeeWX
        if self.first_run:
            if phpdriver=='pdo':
                base_php = SQLuploadGenerator.PHP_PDO % (sqlcolumns,tablename)
            elif phpdriver=='mysqli':
                base_php = SQLuploadGenerator.PHP_MYSQLI % (sqlcolumns,tablename)
            else:
                logerr("unknown PHP MySQL driver '%s'" % phpdriver)
                return

            try:
                conn.execute(SQLuploadGenerator.SQL_CREATE % (tablename,blobtype))
            except Exception as e:
                if log_failure:
                    logerr("could not create table '%s': %s %s" % (
                                             tablename,e.__class__.__name__,e))
                return
            try:
                fn = os.path.join(target_path,'weewxsqlupload.php')
                with open(fn,'wt') as f:
                    f.write(SQLuploadGenerator.PHP_START)
                    f.write(SQLuploadGenerator.PHP_INI % (
                                         'localhost',username,password,dbname))
                    f.write(base_php)
                    f.write(SQLuploadGenerator.PHP_END)
            except OSError as e:
                if log_failure:
                    logerr("could not write %s: %s %s" % (fn,e.__class__.__name__))
                return
        
        # get default actions
        global_actions = generator_dict.get('actions',
                             ['sqlupload','writephp','blockftp','adjustlinks'])
        if isinstance(global_actions,str): global_actions = [global_actions]
        global_preserveext = weeutil.weeutil.to_bool(generator_dict.get(
                                         'preserve_file_name_extension',False))
        global_divide_tag = generator_dict.get('html_divide_tag','html')
        logdbg("global options: actions=%s html_divide_tag='%s'" % (global_actions,global_divide_tag))
        
        # list of link targets to replace
        files_list = self.get_links_to_replace(generator_dict,global_actions)
        if __name__ == '__main__':
            print('------ files_list ------')
            print(files_list)
            print('------------------------')

        # begin transaction
        conn.begin()
        
        ct = 0
        ctc = 0
        ctr = 0
        for section in generator_dict.sections:
            if not self.running: break
            # If `enable` is `False` go to the next entry
            if not weeutil.weeutil.to_bool(
                                   generator_dict[section].get('enable',True)):
                logdbg("Section '%s' not enabled. Skipped." % section)
                continue
            # If `first_run_only` is set and this is not the first run
            # after restart, go to the next entry.
            if (not self.first_run and weeutil.weeutil.to_bool(
                         generator_dict[section].get('first_run_only',False))):
                logdbg("Section '%s' first run only. Skipped." % section)
                continue
            # file name
            file = generator_dict[section].get('file',section)
            # target file
            full_local_path = os.path.join(target_path,file)
            # file name extension
            fext = os.path.splitext(file)[1]
            # Check if file is updated since the last processing
            try:
                if os.path.getmtime(full_local_path)<=sql_last_upload.get_timestamp(file):
                    logdbg("Section '%s': File '%s' was not updated. Skipped." % (section,file))
                    continue
            except (OSError,ArithmeticError,TypeError,ValueError):
                pass
            # debug message
            logdbg("processing section '%s', file '%s'" % (section,file))
            # actions
            # Note: If `actions` is not in the section and so `actions`
            #       becomes `global_actions`, changes to `actions` change
            #       `global_actions` as well. If you want to change `actions`
            #       afterwards you must make a copy of the value explicitely
            #       by using `copy.copy()`.
            actions = generator_dict[section].get('actions',global_actions)
            if isinstance(actions,str): actions = [actions]
            preserveext = weeutil.weeutil.to_bool(generator_dict[section].get(
                            'preserve_file_name_extension',global_preserveext))
            # debug message
            logdbg("actions=%s" % actions)
            logdbg("preserveext=%s" % preserveext)
            #
            x = file.split('/')
            inc_file = '/'.join((['..']*(len(x)-1))+['weewxsqlupload.php'])
            logdbg("include file '%s'" % inc_file)
            php = SQLuploadGenerator.PHP_INCL % (section,inc_file)
            # read file and process
            try:
                # Insert record into the database if it is not already there
                if self.first_run and 'sqlupload' in actions:
                    try:
                        logdbg(sql_ins_str)
                        conn.execute(sql_ins_str,(section,))
                    except Exception as e:
                        logerr(e)
                # Process file according to the content type
                if fext in ('.html','.htm'):
                    # HTML is divided into a constant and a variable part,
                    # and links are adjusted if configured to do so.
                    if 'writephp' in actions and 'sqlupload' in actions:
                        tag = generator_dict[section].get(
                            'html_divide_tag',
                            global_divide_tag
                        )
                    else:
                        tag = 'none'
                    data = self.process_html(full_local_path, php, tag, 
                                files_list if 'adjustlinks' in actions else [])
                elif fext=='.js':
                    # JavaScript: Links are adjusted if configured to do so.
                    data = self.process_js(full_local_path, php, 
                                files_list if 'adjustlinks' in actions else [])
                elif fext in SQLuploadGenerator.OTHER_FILES:
                    # Files of types listed in OTHER_FILES are uploaded as
                    # they are, but their content type is included in the 
                    # PHP file.
                    content_type = self._get_content_type(
                        SQLuploadGenerator.OTHER_FILES[fext],
                        generator_dict[section].get('encoding'))
                    data = self.process_other(full_local_path, php, 
                                                                  content_type)
                else:
                    # files not covered by the special processing above
                    with open(full_local_path,'rb') as f:
                        db_data = f.read()
                    data = (
                        '%s%s%s%s' % (SQLuploadGenerator.PHP_START,php,
                                 SQLuploadGenerator.PHP_ECHO,
                                                   SQLuploadGenerator.PHP_END),
                        db_data,
                        self._get_content_type(
                            generator_dict[section].get('content_type'),
                            generator_dict[section].get('encoding')
                        )
                    )
                # Abort loop in case of program shutdown
                if not self.running: break
                # Transfer data to the server according to configuration
                uploaded, changed, removed = self.transfer(
                        conn,full_local_path,actions,preserveext,sql_upd_str,section,data,sql_last_upload)
                # Statistics
                ct += uploaded
                ctc += changed
                ctr += removed
                # processing timestamp
                # Note: int() always rounds downwards. So add 1 to round upwards.
                sql_last_upload.add_timestamp(file,int(time.time())+1)
                # update #FTP.last
                if 'blockftp' in actions and has_pickle:
                    ftp_last_upload.add(
                        full_local_path,
                        weeutil.ftpupload.sha256sum(full_local_path) if has_hashlib else None
                    )
            
            except (LookupError,TypeError,ValueError,OSError,ArithmeticError) as e:
                if log_failure and not file.endswith('.png'):
                    logerr('%s %s' % (e.__class__.__name__,e))
        
        # commit transaction
        if ct: conn.commit()
        # close database connection
        conn.close()
        
        # save hashes and timestamps
        sql_last_upload.save()
        ftp_last_upload.save()

        # report success
        end_ts = time.time()
        if log_success:
            loginf(
                'Uploaded %s record%s, changed %s file%s, and removed %s file%s in %.2f seconds' % (
                ct,'' if ct==1 else 's',
                ctc,'' if ctc==1 else 's',
                ctr,'' if ctr==1 else 's',
                end_ts-start_ts))

    def get_links_to_replace(self, generator_dict, default_actions):
        """ list of link targets to replace
        
            If the file name extension of the file to process is to be
            replaced by `.php` and there is no re-writing of '.html' to
            `.php` in the web server configuration (e.g. .htaccess file), 
            all the links to that file have to be replaced as well.
            
            Note: Changes to `actions` have an effect on `global_actions`
                  if the key `actions` is not found in the section.
        """
        global_actions = generator_dict.get('actions',default_actions)
        if isinstance(global_actions,str): global_actions = [global_actions]
        global_preserveext = weeutil.weeutil.to_bool(generator_dict.get(
                                         'preserve_file_name_extension',False))
        replace_links = weeutil.weeutil.to_bool(
                         generator_dict.get('replace_links_to_this_file',True))
        files_list = []
        for section in generator_dict.sections:
            # file name
            file = generator_dict[section].get('file',section)
            actions = generator_dict[section].get('actions',global_actions)
            if isinstance(actions,str): actions = [actions]
            if (weeutil.weeutil.to_bool(generator_dict[section].get(
                               'replace_links_to_this_file',replace_links)) and
                not weeutil.weeutil.to_bool(generator_dict[section].get(
                        'preserve_file_name_extension',global_preserveext)) and
                'writephp' in actions and
                '.' in file):
                files_list.append(file)
        return files_list

    def transfer(self, conn, file, actions, preserveext, sql_str, id, data, sql_last_upload):
        """ upload to database and change file """
        if 'sqlupload' in actions:
            # Has data changed?
            if has_hashlib:
                filehash = hashlib.sha256(data[1]).hexdigest()
            else:
                filehash = None
            # upload to database
            if not filehash or filehash!=sql_last_upload.get_hash(id):
                try:
                    mtime = os.path.getmtime(file)
                except OSError:
                    mtime = time.time()
                try:
                    if self.dry_run:
                        print('SQL execute',sql_str)
                        print("      `ID`='%s'" % id)
                        print('-----------------')
                        print(data[1])
                        print('-----------------')
                    else:
                        logdbg(sql_str)
                        conn.execute(sql_str,(data[1],data[2],mtime,id))
                except Exception:
                    return (0,0,0)
                uploaded = 1
            else:
                logdbg("no need to upload id '%s'" % id)
                uploaded = 0
            sql_last_upload.add_hash(id,filehash)
        else:
            uploaded = 0
            if 'writephp' not in actions and 'adjustlinks' in actions:
                # adjust the links only
                if self.dry_run:
                    print('-----------------',file)
                    print(data[1])
                    print('-----------------')
                    return (uploaded,1,0)
                with open(file,'wb') as f:
                    f.write(data[1])
                return (uploaded,1,0)
        # replace extension
        if not preserveext:
            if 'remove' in actions:
                if self.dry_run:
                    print("os.unlink('%s')" % file)
                else:
                    os.unlink(file)
                # If there is nothing to write to file, omit it.
                if 'writephp' not in actions: 
                    return (uploaded,0,1)
            # Change the file extension to .php
            file = get_php_filename(file)
        if 'writephp' in actions and (self.first_run or preserveext):
            # If `preserveext` is `True`, overwrite the original file by
            # the PHP script, otherwise write the PHP script to a separate
            # file using file name extension `.php`.
            if self.dry_run:
                print('-----------------',file)
                print(data[0])
                print('-----------------')
                return (uploaded,1,0)
            with open(file,'wt') as f:
                f.write(data[0])
            return (uploaded,1,0)
        return (uploaded,0,0)

    def process_other(self, file, php, content_type):
        """ process files other than HTML 
        
            This function processes files that cannot be split into a 
            constant and a variable part, for example images. It returns
            the whole content of the file for SQL upload in `db_data`
            and a small pure PHP script (which is to fetch the database 
            record) in `file_data`.
            
            The PHP script sets the MIME type for the HTTP header as
            well, as the web server cannot recognize it from the
            file name extension any more after changing it to `.php`
        """
        with open(file,'rb') as f:
            db_data = f.read()
        file_data = "%s%s%s%s" % (
            SQLuploadGenerator.PHP_START,
            php,
            SQLuploadGenerator.PHP_ECHO,
            SQLuploadGenerator.PHP_END
        )
        return file_data, db_data, content_type

    def process_js(self, file, php, files_list):
        """ process Javascript files 
        
            In JavaScript files, there can be references to files whose
            file name extension is changed to `.php`.
        """
        # Read the JavaScript file
        with open(file,'rt',encoding='utf-8') as f:
            db_data = f.read()
        logdbg('%s: size %d' % (file,len(db_data)))
        # Special replacement in belchertown.js
        if file.endswith('js/belchertown.js'):
            db_data = db_data.replace(
                'replace(/\\/[^\\/]*html$/,"")',
                'replace(/\\/[^\\/]*(html|php)$/,"")'
            )
        # Search the JavaScript file for file references
        sep = None
        nobackslash = True
        slash = False
        txt1 = []
        txt2 = ''
        for c in db_data:
            if sep:
                if sep=='/' and c=='\n':
                    # end of one-line comment
                    txt1.append(txt2)
                    txt1.append(c)
                    txt2 = ''
                    sep = None
                elif c==sep and nobackslash and sep in ('"',"'"):
                    # end of string
                    sep = None
                    for file in files_list:
                        if file in txt2:
                            # one of the references occurs in the JavaScript file
                            new_file = get_php_filename(file)
                            txt2 = txt2.replace(file,new_file)
                    txt1.append(txt2)
                    txt1.append(c)
                    txt2 = ''
                else:
                    txt2 += c
                #slash = False
            else:
                txt1.append(c)
                if c in ('/',) and slash and nobackslash:
                    # start of comment
                    sep = c
                elif c in ('"',"'") and nobackslash:
                    # start of string
                    sep = c
            slash = c=='/' and not slash and nobackslash
            nobackslash = c!='\\' or not nobackslash
        if txt2: txt1.append(txt2)
        logdbg('size of list txt1=%d, size of txt2=%d' % (len(txt1),len(txt2)))
        db_data = ''.join(txt1)
        """
        for file in files_list:
            if file in db_data:
                # one of the references occurs in the JavaScript file
                new_file = '%s.php' % os.path.splitext(file)[0]
                # TODO: do some JavaScript syntax checking
                db_data = db_data.replace(file,new_file)
        """
        # PHP script
        file_data = "%s%s%s%s" % (
            SQLuploadGenerator.PHP_START,
            php,
            SQLuploadGenerator.PHP_ECHO,
            SQLuploadGenerator.PHP_END
        )
        return file_data, db_data.encode('utf-8','ignore'), 'text/javascript'

    def process_html(self, file, php, divide_tag, files_list):
        """ split HTML in constant and variable part 
        
            The file is split at the tag defined by the parameter `divide_tag`.
            It must be a tag that occurs in the file only once. The return
            value `file_data` contains the part from the beginning up to the
            start tag, then the value of the parameter `php`, then the 
            part from the end tag to the end of the file. The return value
            `db_data` contains the part of the file from the start tag to
            the end tag (excluding the tags).
        """
        try:
            # initialize parser
            parser = HTMLdivide(
                '%s%s%s' % (
                    SQLuploadGenerator.PHP_START,
                    SQLuploadGenerator.PHP_ECHO,
                    SQLuploadGenerator.PHP_END
                ),
                files_list,
                divide_tag,
                convert_charrefs=False)
            # feed file into the parser
            with open(file,'rt',encoding='utf-8') as f:
                for line in f:
                    parser.feed(line)
            # get results
            file_data = '%s%s%s%s' % (
                SQLuploadGenerator.PHP_START,
                php,
                SQLuploadGenerator.PHP_END,
                parser.php_data
            )
            db_data = parser.db_data
        except (ValueError,TypeError,LookupError) as e:
            logerr("error parsing HTML file '%s': %s %s" % (file,e.__class__.__name__))
            return None, None, None
        return file_data, db_data.encode('utf-8','ignore'), 'text/html'
        
        file_data = ''
        db_data = ''
        # split file
        inner = False
        with open(file,'rt',encoding='utf-8') as f:
            for line in f:
                if inner:
                    if '</html>' in line:
                        x = line.split('</html>')
                        db_data += x[0]
                        file_data += '</html>%s' % x[1]
                        inner = False
                    else:
                        db_data += line
                else:
                   if '<html' in line:
                       x = line.split('<html')
                       i = x[1].find('>')
                       file_data += '%s<html%s\n' % (x[0],x[1][:i+1])
                       file_data += SQLuploadGenerator.PHP_START
                       file_data += php
                       file_data += SQLuploadGenerator.PHP_ECHO
                       file_data += SQLuploadGenerator.PHP_END
                       db_data += x[1][i+1:]
                       inner = True
                   else:
                       file_data += line
        logdbg('%s %s' % (type(file_data),type(db_data)))
        return file_data, db_data.encode('utf-8','ignore'), 'text/html'
    
    def create_user(self, conn, databasename, tablename):
        try:
            conn.execute("CREATE USER ?@'localhost' IDENTIFIED BY ?",self.phpuser)
            conn.execute("GRANT SELECT ON %s.%s TO ?@'localhost'" % (database_name,table_name),(self.phpuser[0],))
        except Exception as e:
            logerr('%s %s' % (e.__class__.__name__,e))
    
    def merge_skin(self, generator_dict):
        """ merge skin configuration into the SQLupload confiuration """
        global_divide_tag = generator_dict.get('html_divide_tag','html')
        skin_name = generator_dict['merge_skin']
        report_dict = self.config_dict.get('StdReport',configobj.ConfigObj())
        skin_dict = report_dict.get(skin_name)
        if not skin_dict:
            logerr("skin '%s' not found" % skin_name)
            return
        skin_dir = skin_dict.get('skin')
        if not skin_dir:
            logerr("no skin directory specified for skin '%s'" % skin_name)
        skin_path = os.path.join(
            self.config_dict.get('WEEWX_ROOT','.'),
            report_dict.get('SKIN_ROOT','.'),
            skin_dir,
            'skin.conf'
        )
        skin_dict = configobj.ConfigObj(skin_path)
        logdbg('skin_path=%s' % skin_path)
        if __name__ == '__main__':
            logdbg('skin_dict=%s' % skin_dict)
        # CheetahGenerator files
        for sec,val in skin_dict.get('CheetahGenerator',configobj.ConfigObj()).get('ToDate',configobj.ConfigObj()).items():
            logdbg('merge_skin %s %s' % (sec,val))
            if sec in generator_dict:
                logdbg("'%s' already in generator_dict" % sec)
            elif weeutil.weeutil.to_bool(val.get('generate_once',False)):
                logdbg("'%s' generate_once" % sec)
            else:
                stale_age = weeutil.weeutil.to_int(val.get('stale_age',0))
                if stale_age<=300:
                    template = val.get('template')
                    if template:
                        # remove .tmpl
                        template = os.path.splitext(template)[0]
                        generator_dict[sec] = {
                            'file':template
                        }
                        if 'encoding' in val:
                            generator_dict[sec]['encoding'] = val['encoding']
        # ImageGenerator files
        image_dict = skin_dict.get('ImageGenerator',configobj.ConfigObj())
        for sec in image_dict.sections:
            logdbg('merge_skin %s' % sec)
            val = image_dict[sec]
            for subsec in val.sections:
                generator_dict['%s-%s' % (sec,subsec)] = {
                    'file':'%s.png' % subsec,
                    'content_type':'image/png',
                }
        # Belchertown HighCharts files
        if 'user.belchertown.HighchartsJsonGenerator' in skin_dict.get(
                  'Generators',configobj.ConfigObj()).get('generator_list',[]):
            graphs_path = os.path.join(
                self.config_dict.get('WEEWX_ROOT','.'),
                report_dict.get('SKIN_ROOT','.'),
                skin_dir,
                'graphs.conf'
            )
            graphs_dict = configobj.ConfigObj(graphs_path)
            logdbg('graphs_path=%s' % graphs_path)
            if __name__ == '__main__':
                logdbg('graphs_dict=%s' % graphs_dict)
            for sec in graphs_dict.sections:
                logdbg('merge_skin %s' % sec)
                gensec = '%s-graphs' % sec
                if gensec in generator_dict:
                    logdbg("'%s' already in generator_dict" % gensec)
                else:
                    generator_dict[gensec] = {
                        'file':'json/%s.json' % sec,
                        'content_type':'application/json',
                        'encoding':'utf-8',
                    }

    def _get_content_type(self, content_type, encoding):
        """ put content type and encoding together """
        if encoding in ('html_entities','strict_ascii','normalized_ascii',None):
            return content_type
        if encoding=='utf8': encoding = 'utf-8'
        return '%s; charset=%s' % (content_type,encoding)


class SQLlastUpload(object):
    """ manage state of SQL uploads """
    
    def __init__(self, target_path):
        self.timestamp_file_path = os.path.join(target_path, '#SQLupload.last')
        self.timestamp_dict, self.hash_dict = self._load()
    
    def add_hash(self, id, hash):
        self.hash_dict[id] = hash
    
    def get_hash(self, id):
        return self.hash_dict.get(id)
    
    def add_timestamp(self, file, timestamp):
        self.timestamp_dict[file] = timestamp
    
    def get_timestamp(self, file):
        return self.timestamp_dict.get(file,0)
    
    def _load(self):
        """ Reads time, members, and hashes of the last upload """
        hash_dict = dict()
        timestamp_dict = dict()
        hash_fn = self.timestamp_file_path
        try:
            with open(hash_fn,'rt') as f:
                reply = json.load(f)
            logdbg("successfully loaded hash file '%s'" % hash_fn)
            hash_dict = reply.get('hash',dict())
            timestamp_dict = reply.get('timestamp',dict())
        except FileNotFoundError:
            logdbg("hash file '%s' not found (no problem at first run)" % hash_fn)
        except (OSError,ValueError) as e:
            logdbg("error loading hash file '%s': %s %s" % (hash_fn,e.__class__.__name__,e))
        return timestamp_dict, hash_dict
    
    def save(self):
        """ Saves time, members, and hashes of the current upload """
        hash_fn = self.timestamp_file_path
        try:
            with open(hash_fn,'wt') as f:
                json.dump({'hash':self.hash_dict,
                                'timestamp':self.timestamp_dict},
                                                         f,ensure_ascii=False)
            logdbg("successfully saved hash file '%s'" % hash_fn)
        except (OSError,ValueError) as e:
            logdbg("error saving hash file '%s': %s %s" % (
                                                hash_fn,e.__class__.__name__))

class FTPlastUpload(object):
    """ manage the state file of the FTP upload generator 
    
        This is to prevent files from being uploaded by both the SQL
        upload generator and the FTP upload generator.

        Timestamp is not updated here as this is not the FTP upload
        generator and no files were uploaded by FTP.
        
        Caution!
        If WeeWX changes the structure of the #FTP.last file, this class
        requires an update, too.
    """

    def __init__(self, target_path):
        self.timestamp_file_path = os.path.join(target_path, '#FTP.last')
        self.changed = False
        self.timestamp, self.fileset, self.hashdict = self._load()
    
    def add(self, full_local_path, filehash):
        """ Add or update item """
        self.fileset.add(full_local_path)
        self.hashdict[full_local_path] = filehash
        self.changed = True
        logdbg("FTP state updated for %s with %s" % (full_local_path,filehash))

    def _load(self):
        """ Reads the time and members of the last upload from the local root
            Copyright (C) Tom Keffer
        """
        if not has_pickle: return 0, set(), dict()
        try:
            with open(self.timestamp_file_path, "rb") as f:
                timestamp = pickle.load(f)
                fileset = pickle.load(f)
                hashdict = pickle.load(f)
            logdbg("successfully loaded FTP upload generator's state file")
        except (OSError, pickle.PickleError, AttributeError):
            timestamp = 0
            fileset = set()
            hashdict = dict()
        return timestamp, fileset, hashdict
    
    def save(self):
        """ Saves the time and members of the last upload in the local root
            Copyright (C) Tom Keffer
        """
        if self.changed and has_pickle:
            with open(self.timestamp_file_path, "wb") as f:
                pickle.dump(self.timestamp, f)
                pickle.dump(self.fileset, f)
                pickle.dump(self.hashdict, f)
            logdbg("successfully saved FTP upload generator's state file")
        """
        with open(self.timestamp_file_path+'.json',"wt") as f:
            json.dump({'timestamp':self.timestamp,'fileset':list(self.fileset),'hashdict':self.hashdict},f,ensure_ascii=False, indent=4)
        """


##############################################################################
#    Service to upload the LOOP packets to the database for live display     #
##############################################################################

class SQLRESTful(weewx.restx.StdRESTful):
    """ service to upload the LOOP packet using SQL 
    
        Note: Shutdown handling is included in the base class.
    """

    def __init__(self, engine, config_dict):
        super(SQLRESTful, self).__init__(engine, config_dict)
        site_dict = weewx.restx.get_site_dict(config_dict, 'SQLupload')
        if site_dict is None: return
        try:
            site_dict['manager_dict'] = weewx.manager.get_manager_dict_from_config(config_dict, 'wx_binding')
        except weewx.UnknownBinding:
            pass
        binding = site_dict.pop('binding')
        if not isinstance(binding,list): binding = [binding]
        binding = [i.upper() for i in binding]
        self.loop_queue = queue.Queue(5)
        self.loop_thread = SQLloopThread(self.loop_queue, **site_dict)
        self.loop_thread.start()
        if __name__ != '__main__':
            if 'LOOP' in binding:
                self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)
            if 'ARCHIVE' in binding:
                self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

    def new_loop_packet(self, event):
        try:
            packet = event.packet.copy()
            packet['#TYPE'] = 'LOOP'
            self.loop_queue.put(packet,timeout=1)
        except queue.Full:
            logerr('Queue is full. Thread died?')

    def new_archive_record(self, event):
        try:
            record = event.record.copy()
            record['#TYPE'] = 'ARCHIVE'
            self.loop_queue.put(record,timeout=5)
        except queue.Full:
            logerr('Queue is full. Thread died?')


class SQLloopThread(weewx.restx.RESTThread):
    """ thread to upload the LOOP packet using SQL 
    
        Note: Shutdown handling is included in the base class.
    """

    def __init__(self, q, 
              host=None, port=3306,
              username=None, password=None,
              database_name=None, table_name=None,
              unit_system='US',
              dry_run=False,
              skip_upload=False, manager_dict=None,
              log_success=True,log_failure=True):
        super(SQLloopThread, self).__init__(q,
                                          protocol_name='SQL',
                                          manager_dict=manager_dict,
                                          log_success=log_success,
                                          log_failure=log_failure,
                                          skip_upload=skip_upload)
        # If `dry_run` ist set, print out the SQL statements instead of
        # executing them.
        self.dry_run = dry_run
        # database account data
        self.dbhost = host
        self.dbport = port
        self.dbuser = username
        self.dbpassword = password
        self.dbname = database_name
        self.dbtable = table_name
        # unit system to use for output
        self.unit_system = weewx.units.unit_constants.get(unit_system,weewx.METRIC)
        # prepare SQL statements
        self.sql_ins_str = SQLuploadGenerator.SQL_INSERT % self.dbtable
        self.sql_upd_str = SQLuploadGenerator.SQL_UPDATE % self.dbtable
        # logging
        loginf("%s version %s" % (self.__class__.__name__,VERSION))
        loginf("SQL loop packet upload using unit system %s" % weewx.units.unit_nicknames.get(self.unit_system))
        # database connection
        self.conn = None

    def process_record(self, record, dbmanager):
        """ Process loop packet
        
            This one differs from the base one by not using urllib functions
        """
        # Get the full record by querying the database ...
        _full_record = self.get_record(record, dbmanager)
        # ... check it ...
        self.check_this_record(_full_record)
        # ... get the Request to go with it...
        eventtype = _full_record.pop('#TYPE',None)
        if eventtype not in ('LOOP','ARCHIVE'):
            raise weewx.restx.AbortedPost("Invalid data type %s" % eventtype)
        _request = {'id':eventtype,'mtime':_full_record.get('dateTime',time.time())}
        #  ... get any POST payload...
        _payload = self.get_post_body(_full_record)
        # ... add a proper Content-Type if needed...
        if _payload:
            data = _payload[0]
            _request['Content-Type'] = _payload[1]
        else:
            data = None
        # ... check to see if this is just a drill...
        if self.skip_upload:
            raise weewx.restx.AbortedPost("Skip post")
        # ... then, finally, post it
        self.post_with_retries(_request, data)
    
    def post_with_retries(self, request, data):
        """ upload data 
        
            The name of the function results from the base class. For 
            uploading LOOP packets by SQL here it is no use to re-try
            as the packets arrive quite frequent.
        """
        # record id to upload to
        id = request['id']
        # modification time of the record
        mtime = request['mtime']
        # check database connection and open it if closed
        if self.conn:
            is_newly_opened = False
        elif self.dry_run:
            self.conn = ConnTest()
            is_newly_opened = True
        else:
            # connect to the database
            self.conn = weedb.mysql.connect(
                host=self.dbhost,
                user=self.dbuser,
                password=self.dbpassword,
                database_name=self.dbname,
                port=self.dbport
            )
            if not self.conn:
                if self.log_failure:
                    logerr("error opening database connection")
                return
            is_newly_opened = True
        # execute SQL statements and upload data
        try:
            self.conn.begin()
            if is_newly_opened:
                self.conn.execute(self.sql_ins_str,(id,))
            self.conn.execute(self.sql_upd_str,(data,request['Content-Type'],mtime,id))
            self.conn.commit()
        except Exception as e:
            if self.log_failure:
                logerr("error uploading data: %s %s" % (e.__class__.__name__,e))
            # in case of errors close the database connection in order to have
            # it re-opened later on
            self.conn.close()
            self.conn = None
    
    def get_post_body(self, record):
        """ convert record as required for upload
        """
        _record = weewx.units.to_std_system(record, self.unit_system)
        data = json.dumps(_record,ensure_ascii=False)
        return data, 'application/json; charset=utf-8'
    
    def format_url(self, _):
        """ no URL involved """
        return None


# log version info at startup
loginf("%s version %s" % ("SQLupload",VERSION))
logdbg("has_hashlib=%s, has_pickle=%s" % (has_hashlib,has_pickle))


if __name__ == '__main__':

    config_dict = configobj.ConfigObj({
        'log_success':True,
        'log_failure':True,
        'debug':1,
        'WEEWX_ROOT':'.',
        'StdReport':{
            'SKIN_ROOT':'./skins',
            'Testskin':{
                'skin':'Testskin'
                #'skin':'/etc/weewx/skins/Seasons'
            },
            'FTP': {
                'skin':'Ftp',
            }
        },
        'StdRESTful': {
            'SQLupload': {
                'binding':['LOOP','ARCHIVE'],
                'unit_system':'METRIC',
            }
        }
    })
    config_dict['WEEWX_ROOT'] = os.getcwd()
    stn_info = configobj.ConfigObj({
        
    })
    account = configobj.ConfigObj('../password.txt')
    test_skin_dict = configobj.ConfigObj('./sqlupload-test.conf')
    skin_dict = configobj.ConfigObj()
    skin_dict.update(config_dict)
    skin_dict.update(test_skin_dict)
    skin_dict.update(account)
    config_dict['StdReport']['HTML_ROOT'] = test_skin_dict['HTML_ROOT']
    config_dict['StdRESTful']['SQLupload'].update(account)

    if True:
    
        # Test upload of skins
        gen_ts = time.time()
        first_run = True
        gen =  SQLuploadGenerator(config_dict, skin_dict, gen_ts, first_run, stn_info)
        gen.start()
    
    else:
    
        # Test upload of observation data
        class Event(object):
            def __init__(self):
                self.packet = {'dateTime':time.time(),'usUnits':1,'outTemp':65.0}
        gen = SQLRESTful({},config_dict)
        gen.new_loop_packet(Event())
        time.sleep(2)
        gen.shutDown()
