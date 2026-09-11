from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strandarr.utils.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
Session = sessionmaker(bind=engine)
