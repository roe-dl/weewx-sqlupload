#!/usr/bin/python3
# WeeWX generator to upload skins to using a database
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

VERSION = "0.1"

import os.path
import configobj
import time
import html.parser
import json

try:
    import hashlib
    has_hashlib=True
except ImportError:
    has_hashlib=False

if __name__ == '__main__':
    import sys
    sys.path.append('/usr/share/weewx')

import weewx
import weewx.reportengine
import weeutil.weeutil
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
    log = logging.getLogger("user.svg2png")
    def logdbg(msg):
        log.debug(msg)
    def loginf(msg):
        log.info(msg)
    def logerr(msg):
        log.error(msg)

class HTMLdivide(html.parser.HTMLParser):

    def __init__(self, php, files_list, divide_tag='html', convert_charrefs=True):
        super(HTMLdivide,self).__init__(convert_charrefs=convert_charrefs)
        self.php_data = ''
        self.db_data = ''
        self.inner = divide_tag!='none'
        self.divide_tag = divide_tag
        self.php_script = php
        self.files = files_list

    def handle_starttag(self, tag, attrs):
        if tag=='a':
            # replace href to HTML by PHP
            for idx, val in enumerate(attrs):
                if val[0]=='href':
                    href = val[1].split('#')
                    if self.isinfiles(href[0]):
                        href[0] = href[0].replace('.html','.php')
                        attrs[idx] = ('href','#'.join(href))
        s = '<%s %s>' % (tag,' '.join('%s="%s"' % i for i in attrs))
        if self.inner:
            self.db_data += s
        else:
            self.php_data += s
        if tag==self.divide_tag:
            self.inner = True
            self.php_data += self.php_script
    
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
        s = '&#%s' % name
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
        def begin(self):
            print('SQL begin')
        def commit(self):
            print('SQL commit')
        def execute(self, sql, attrs=()):
            print('SQL execute %s %s' % (sql,attrs))
        def close(self):
            print('SQL close')


class SQLuploadGenerator(weewx.reportengine.ReportGenerator):

    PHP_PDO = '''<?php
  include "%%s";
  $id="%%s";
  $pdo = new PDO(
    "mysql:host=localhost;dbname=$dbname",
    $user,
    $password
  );
  $sql = "SELECT * FROM %s WHERE `ID`=?";
  $statement = $pdo->prepare($sql); 
  $statement->execute([$id]);
  while($row = $statement->fetch()) {
    echo $row["TEXT"];
  }
  $pdo = null;
?>
'''
    PHP_MYSQLI = '''<?php
  include "%%s";
  $id="%%s";
  $pdo = new mysqli("localhost",$user,$password,$dbname);
  $sql = "SELECT * FROM %s WHERE `ID`='" . $id . "'";
  $reply = $pdo->query($sql);
  while($row = $reply->fetch_assoc()) {
    echo $row["TEXT"];
  }
  $pdo->close();
?>
'''
    PHP_INI = '''<?php
  $dbhost = "%s";
  $dbuser = "%s";
  $dbpassword = "%s";
  $dbname = "%s";
?>
'''

    SQL_UPDATE = 'UPDATE %s SET `TEXT`=? WHERE `ID`=?'
    SQL_INSERT = 'INSERT IGNORE INTO %s(`ID`,`TEXT`) VALUES (?,?)'
    SQL_CREATE = 'CREATE TABLE IF NOT EXISTS %s(`ID` CHAR(32) PRIMARY KEY, `TEXT` BLOB)'
    
    def __init__(self, config_dict, skin_dict, gen_ts, first_run, stn_info, record=None):
        super(SQLuploadGenerator,self).__init__(config_dict, skin_dict, gen_ts, first_run, stn_info, record)
        self.running = True
        self.phpuser = ('weewxphpuser','Wcw4nNiQHvvNVAwzFogj')

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
        hash_fn = os.path.join(target_path,'#SQLupload.last')

        # configuration section for this generator
        generator_dict = self.skin_dict.get('SQLuploadGenerator',configobj.ConfigObj())
        self.dry_run = generator_dict.get('dry_run',False)
        if 'merge_skin' in generator_dict:
            self.merge_skin(generator_dict)
        if __name__ == '__main__':
            print('---- generator_dict ----')
            print(json.dumps(generator_dict,indent=4,ensure_ascii=False))
        
        # database 
        dbhost = self.skin_dict.get('host')
        dbport = weeutil.weeutil.to_int(self.skin_dict.get('port',3306))
        dbname = self.skin_dict.get('database_name')
        username = self.skin_dict.get('username')
        password = self.skin_dict.get('password')
        tablename = self.skin_dict.get('table_name')
        phpdriver = self.skin_dict.get('php_mysql_driver','PDO').lower()
        sql_upd_str = SQLuploadGenerator.SQL_UPDATE % tablename
        sql_ins_str = SQLuploadGenerator.SQL_INSERT % tablename
        
        is_new_database = None
        
        start_ts = time.time()
        
        # Hashes of the data uploaded during the last run
        hash_dict = dict()
        try:
            with open(hash_fn,'rt') as f:
                hash_dict = json.load(f)
        except (OSError,ValueError):
            pass
        
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
            try:
                conn.execute(SQLuploadGenerator.SQL_CREATE % tablename)
            except Exception as e:
                if log_failure:
                    logerr("could not create table '%s': %s %s" % (
                                             tablename,e.__class__.__name__,e))
                return
            try:
                fn = os.path.join(target_path,'weewxsqlupload.php')
                with open(fn,'wt') as f:
                    f.write(SQLuploadGenerator.PHP_INI % (
                                         'localhost',username,password,dbname))
            except OSError as e:
                if log_failure:
                    logerr("could not write %s: %s %s" % (fn,e.__class__.__name__))
                return
        
        if phpdriver=='pdo':
            base_php = SQLuploadGenerator.PHP_PDO % tablename
        elif phpdriver=='mysqli':
            base_php = SQLuploadGenerator.PHP_MYSQLI % tablename
        else:
            logerr("unknown PHP MySQL driver '%s'" % phpdriver)
            return

        replace_ext = generator_dict.get('replace_extension',True)
        files_list = []
        for section in generator_dict.sections:
            # file name
            file = generator_dict[section].get('file',section)
            if generator_dict[section].get('replace_extension',replace_ext) and '.' in file:
                files_list.append(file)

        # begin transaction
        conn.begin()
        
        ct = 0
        global_divide_tag = generator_dict.get('html_divide_tag','html')
        global_write_php = weeutil.weeutil.to_bool(generator_dict.get('write_php',True))
        for section in generator_dict.sections:
            if not self.running: break
            # file name
            file = generator_dict[section].get('file',section)
            # target file
            fn = os.path.join(target_path,file)
            # debug message
            logdbg("processing section '%s', file '%s'" % (section,file))
            # whether to replace the file name extension
            if file in files_list:
                replace_ext = '.%s' % file.split('.')[-1]
            else:
                replace_ext = None
            #
            x = file.split('/')
            inc_file = '/'.join((['..']*(len(x)-1))+['weewxsqlupload.php'])
            logdbg("include file '%s'" % inc_file)
            php = base_php % (inc_file,section)
            # read file and process
            try:
                if self.first_run:
                    try:
                        logdbg(sql_ins_str)
                        conn.execute(sql_ins_str,(section,''))
                    except Exception as e:
                        logerr(e)
                if file.endswith('.html'):
                    tag = generator_dict[section].get(
                        'html_divide_tag',
                        global_divide_tag
                    )
                    data = self.process_html(fn, php, tag, files_list)
                elif file.endswith('json'):
                    data = self.process_other(fn, php, 'application/json')
                elif file.endswith('xml'):
                    data = self.process_other(fn, php, 'application/xml')
                else:
                    with open(fn,'rb') as f:
                        db_data = f.read()
                    data = (php,db_data)
                if not self.running: break
                if not weeutil.weeutil.to_bool(generator_dict[section].get('write_php',global_write_php)):
                    data[0] = None
                if self.transfer(conn,fn,sql_upd_str,section,data,replace_ext,hash_dict):
                    ct += 1
            except (LookupError,TypeError,ValueError,OSError) as e:
                if log_failure:
                    logerr('%s %s' % (e.__class__.__name__,e))
        
        # commit transaction
        if ct: conn.commit()
        # close database connection
        conn.close()
        
        # save hashes
        try:
            with open(hash_fn,'wt') as f:
                json.dump(hash_dict,f,ensure_ascii=False)
        except (OSError,ValueError):
            pass

        # report success
        end_ts = time.time()
        if log_success:
            loginf('Uploaded %s file%s in %.2f seconds' % (ct,'' if ct==1 else 's',end_ts-start_ts))

    def transfer(self, conn, file, sql_str, id, data, replace_ext, hash_dict):
        """ upload to database and change file """
        # Has data changed?
        if has_hashlib:
            filehash = hashlib.sha256(data[1]).hexdigest()
        else:
            filehash = None
        # upload to database
        if not filehash or filehash!=hash_dict.get(id):
            try:
                if self.dry_run:
                    print('SQL execute',sql_str)
                    print("      `ID`='%s'" % id)
                    print('-----------------')
                    print(data[1])
                    print('-----------------')
                else:
                    logdbg(sql_str)
                    conn.execute(sql_str,(data[1],id))
            except Exception:
                return False
        else:
            logdbg("no need to upload id '%s'" % id)
        hash_dict[id] = filehash
        # replace extension
        if replace_ext:
            if self.dry_run:
                print("os.unlink('%s')" % file)
            else:
                os.unlink(file)
            # If there is nothing to write to file, omit it.
            if not data[0]: return True
            # Change the file extension to .php
            file = file.replace(replace_ext,'.php')
        # in case of success change file to use the database
        if self.dry_run:
            print('-----------------',file)
            print(data[0])
            print('-----------------')
            return
        with open(file,'wt') as f:
            f.write(data[0])
        return True
    
    def process_other(self, file, php, content_type):
        with open(file,'rb') as f:
            db_data = f.read()
        file_data = """<?php
  header('Content-type: %s');
?>
%s""" % (content_type,php)
        return file_data, db_data

    def process_html(self, file, php, divide_tag, files_list):
        """ split HTML in constant and variable part """
        parser = HTMLdivide(php,files_list,divide_tag,convert_charrefs=False)
        with open(file,'rt',encoding='utf-8') as f:
            for line in f:
                parser.feed(line)
        file_data = parser.php_data
        db_data = parser.db_data
        return file_data, db_data.encode('utf-8','ignore')
        
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
                       file_data += php
                       db_data += x[1][i+1:]
                       inner = True
                   else:
                       file_data += line
        logdbg('%s %s' % (type(file_data),type(db_data)))
        return file_data, db_data.encode('utf-8','ignore')
    
    def create_user(self, conn, databasename, tablename):
        try:
            conn.execute("CREATE USER ?@'localhost' IDENTIFIED BY ?",self.phpuser)
            conn.execute("GRANT SELECT ON %s.%s TO ?@'localhost'" % (database_name,table_name),(self.phpuser[0],))
        except Exception as e:
            logerr('%s %s' % (e.__class__.__name__,e))
    
    def merge_skin(self, generator_dict):
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
        if __name__ == '__main__':
            skin_path = os.path.join(report_dict.get('SKIN_ROOT','.'),skin_dir,'skin.conf')
        else:
            skin_path = os.path.join(self.config_dict.get('WEEWX_ROOT','.'),report_dict.get('SKIN_ROOT','.'),skin_dir,'skin.conf')
        skin_dict = configobj.ConfigObj(skin_path)
        logdbg('skin_path=%s' % skin_path)
        logdbg('skin_dict=%s' % skin_dict)
        for sec,val in skin_dict.get('CheetahGenerator',configobj.ConfigObj()).get('ToDate',configobj.ConfigObj()).items():
            logdbg('merge_skin %s %s' % (sec,val))
            if sec in generator_dict:
                logdbg("'%s' already in generator_dict")
            else:
                stale_age = weeutil.weeutil.to_int(val.get('stale_age',0))
                if stale_age<86400:
                    template = val.get('template')
                    if template:
                        template = template.replace('.tmpl','')
                        generator_dict[sec] = {
                            'file':template
                        }

if __name__ == '__main__':

    config_dict = configobj.ConfigObj({
        'log_success':True,
        'log_failure':True,
        'debug':1,
        'WEEWX_ROOT':'/',
        'StdReport':{
            'SKIN_ROOT':'./test/skins',
            'Testskin':{
                'skin':'Testskin'
            }
        }
    })
    stn_info = configobj.ConfigObj({
        
    })
    account = configobj.ConfigObj('./password.txt')
    test_skin_dict = configobj.ConfigObj('./sqlupload-test.conf')
    skin_dict = configobj.ConfigObj()
    skin_dict.update(config_dict)
    skin_dict.update(test_skin_dict)
    skin_dict.update(account)

    gen_ts = time.time()
    first_run = True
    gen =  SQLuploadGenerator(config_dict, skin_dict, gen_ts, first_run, stn_info)
    gen.start()
