from flask.ext.script import Manager
from flask.ext.migrate import Migrate, MigrateCommand
from site.models import db
from site.app_factory import create_app

app = create_app(database=db, initialize_blueprints=False)


migrate = Migrate(app, db)

manager = Manager(app)
manager.add_command('db', MigrateCommand)

if __name__ == '__main__':
    manager.run()
