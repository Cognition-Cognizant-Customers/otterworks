package com.otterworks.portal.feedback;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;
import software.amazon.awssdk.services.dynamodb.model.ScanRequest;
import software.amazon.awssdk.services.dynamodb.model.UpdateItemRequest;

/**
 * DynamoDB persistence for the feedback context.
 *
 * <p>Table layout: partition key {@code pk} (N) = feedback id. Item {@code pk=0} is the atomic
 * id-allocation counter. Volumes are portal-scale, so reads use strongly consistent Scans on
 * the base table, which keeps read-after-write behavior identical to the monolith.
 */
public class DynamoFeedbackStore implements FeedbackStore {

    static final String COUNTER_PK = "0";

    private final DynamoDbClient client;
    private final String tableName;

    public DynamoFeedbackStore(DynamoDbClient client, String tableName) {
        this.client = client;
        this.tableName = tableName;
    }

    @Override
    public long nextId() {
        var response = client.updateItem(UpdateItemRequest.builder()
                .tableName(tableName)
                .key(Map.of("pk", AttributeValue.fromN(COUNTER_PK)))
                .updateExpression("ADD seq :one")
                .expressionAttributeValues(Map.of(":one", AttributeValue.fromN("1")))
                .returnValues("UPDATED_NEW")
                .build());
        return Long.parseLong(response.attributes().get("seq").n());
    }

    @Override
    public void put(Feedback f) {
        client.putItem(PutItemRequest.builder()
                .tableName(tableName)
                .item(Map.of(
                        "pk", AttributeValue.fromN(Long.toString(f.getId())),
                        "userId", AttributeValue.fromS(f.getUserId()),
                        "rating", AttributeValue.fromN(Integer.toString(f.getRating())),
                        "message", AttributeValue.fromS(f.getMessage()),
                        "createdAt", AttributeValue.fromS(f.getCreatedAt().toString())))
                .build());
    }

    @Override
    public List<Feedback> findByUserId(String userId) {
        return client.scanPaginator(ScanRequest.builder()
                        .tableName(tableName)
                        .consistentRead(true)
                        .filterExpression("userId = :u")
                        .expressionAttributeValues(Map.of(":u", AttributeValue.fromS(userId)))
                        .build())
                .items().stream()
                .map(DynamoFeedbackStore::fromItem)
                .collect(Collectors.toList());
    }

    @Override
    public List<Feedback> findAll() {
        return client.scanPaginator(ScanRequest.builder().tableName(tableName).consistentRead(true).build())
                .items().stream()
                .filter(item -> !COUNTER_PK.equals(item.get("pk").n()))
                .map(DynamoFeedbackStore::fromItem)
                .collect(Collectors.toList());
    }

    private static Feedback fromItem(Map<String, AttributeValue> item) {
        return new Feedback(
                Long.parseLong(item.get("pk").n()),
                item.get("userId").s(),
                Integer.parseInt(item.get("rating").n()),
                item.get("message").s(),
                Instant.parse(item.get("createdAt").s()));
    }
}
