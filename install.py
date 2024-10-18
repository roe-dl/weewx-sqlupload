# installer SQLupload
# Copyright 2024 Johanna Roedenbeck
# Distributed under the terms of the GNU Public License (GPLv3)

from weecfg.extension import ExtensionInstaller

def loader():
    return SQLuploadInstaller()

class SQLuploadInstaller(ExtensionInstaller):
    def __init__(self):
        super(SQLuploadInstaller, self).__init__(
            version="0.3",
            name='SQLupload',
            description='upload to database',
            author="Johanna Roedenbeck",
            author_email="",
            config={
                'StdReport':{
                    'SQLupload':{
                        'skin':'SQLupload',
                        'host':'replace_me',
                        'username':'replace_me',
                        'password':'replace_me',
                        'database_name':'replace_me',
                        'table_name':'replace_me'
                    }
                }
            },
            files=[
                ('bin/user', ['bin/user/sqlupload.py']),
                ('skins/SQLupload', ['skins/SQLupload/skin.conf']),
            ]
        )
