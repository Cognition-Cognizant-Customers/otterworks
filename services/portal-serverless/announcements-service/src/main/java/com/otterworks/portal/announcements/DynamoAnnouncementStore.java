package com.otterworks.portal.announcements;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.GetItemRequest;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;
import software.amazon.awssdk.services.dynamodb.model.ScanRequest;
import software.amazon.awssdk.services.dynamodb.model.UpdateItemRequest;

/**
 * DynamoDB persistence for the announcements context.
 *
 * <p>Table layout: partition key {@code pk} (N) = announcement id. Item {@code pk=0} is the
 * atomic id-allocation counter (parity with the monolith's H2 identity column). Volumes are
 * portal-scale, so list operations use Scan.
 */
public class DynamoAnnouncementStore implements AnnouncementStore {

    static final String COUNTER_PK = "0";

    private final DynamoDbClient client;
    private final String tableName;

    public DynamoAnnouncementStore(DynamoDbClient client, String tableName) {
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
    public void put(Announcement a) {
        client.putItem(PutItemRequest.builder()
                .tableName(tableName)
                .item(Map.of(
                        "pk", AttributeValue.fromN(Long.toString(a.getId())),
                        "title", AttributeValue.fromS(a.getTitle()),
                        "body", AttributeValue.fromS(a.getBody()),
                        "published", AttributeValue.fromBool(a.isPublished()),
                        "createdAt", AttributeValue.fromS(a.getCreatedAt().toString())))
                .build());
    }

    @Override
    public Optional<Announcement> find(long id) {
        var response = client.getItem(GetItemRequest.builder()
                .tableName(tableName)
                .key(Map.of("pk", AttributeValue.fromN(Long.toString(id))))
                .build());
        if (!response.hasItem() || response.item().isEmpty()) {
            return Optional.empty();
        }
        return Optional.of(fromItem(response.item()));
    }

    @Override
    public List<Announcement> findAll() {
        return client.scanPaginator(ScanRequest.builder().tableName(tableName).build())
                .items().stream()
                .filter(item -> !COUNTER_PK.equals(item.get("pk").n()))
                .map(DynamoAnnouncementStore::fromItem)
                .collect(Collectors.toList());
    }

    private static Announcement fromItem(Map<String, AttributeValue> item) {
        return new Announcement(
                Long.parseLong(item.get("pk").n()),
                item.get("title").s(),
                item.get("body").s(),
                item.get("published").bool(),
                Instant.parse(item.get("createdAt").s()));
    }
}
