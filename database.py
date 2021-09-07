from datetime import datetime

import mongoengine

from config import CONFIG

mongoengine.connect(host=CONFIG.database.host)


class Block(mongoengine.Document):
    user_id = mongoengine.IntField(primary_key=True)
    reason = mongoengine.StringField(required=True)
    moderator_id = mongoengine.IntField(required=True)
    timestamp = mongoengine.DateTimeField(
        required=True, default=datetime.utcnow
    )


class Report(mongoengine.Document):
    reason = mongoengine.StringField(required=True)
    user_id = mongoengine.IntField(required=True)
    reporter_id = mongoengine.IntField(required=True)
    timestamp = mongoengine.DateTimeField(
        required=True, default=datetime.utcnow
    )
    message_id = mongoengine.IntField()
    reviewed = mongoengine.BooleanField(required=True, default=False)
