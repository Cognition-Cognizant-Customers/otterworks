package com.otterworks.portal.moderation;

import java.util.Map;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;

public final class DynamoModerationStore implements ModerationStore {

    private final DynamoDbClient client;
    private final String tableName;

    public DynamoModerationStore(DynamoDbClient client, String tableName) {
        this.client = client;
        this.tableName = tableName;
    }

    @Override
    public boolean putIfAbsent(ModerationRecord record) {
        try {
            client.putItem(PutItemRequest.builder()
                    .tableName(tableName)
                    .item(Map.of(
                            "idempotencyKey", AttributeValue.fromS(record.idempotencyKey()),
                            "feedbackId", AttributeValue.fromN(Long.toString(record.feedbackId())),
                            "userId", AttributeValue.fromS(record.userId()),
                            "status", AttributeValue.fromS(record.status()),
                            "reason", AttributeValue.fromS(record.reason()),
                            "reviewedAt", AttributeValue.fromS(record.reviewedAt().toString())))
                    .conditionExpression("attribute_not_exists(idempotencyKey)")
                    .build());
            return true;
        } catch (ConditionalCheckFailedException duplicate) {
            return false;
        }
    }
}
