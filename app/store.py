import os

from pymongo import MongoClient

client = MongoClient(os.getenv("MONGO_URL", "mongodb://localhost:27017"), serverSelectionTimeoutMS=3000)
generations = client["ct200"]["generations"]


def get_store():
    return generations
