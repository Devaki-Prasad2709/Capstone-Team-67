from kafka import KafkaProducer
import json

KAFKA_BROKER = "localhost:9092"

def get_producer():
    return KafkaProducer(
        bootstrap_servers=KAFKA_BROKER,
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )