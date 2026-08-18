package com.otterworks.portal.projection;

import java.util.Map;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.Put;
import software.amazon.awssdk.services.dynamodb.model.TransactWriteItem;
import software.amazon.awssdk.services.dynamodb.model.TransactWriteItemsRequest;
import software.amazon.awssdk.services.dynamodb.model.TransactionCanceledException;
import software.amazon.awssdk.services.dynamodb.model.Update;

/**
 * DynamoDB projection table: partition key {@code pk} (S).
 *
 * <ul>
 *   <li>{@code stats} — running {@code cnt} and {@code ratingSum} (average = sum/cnt)</li>
 *   <li>{@code evt#<eventId>} — consumer dedupe marker (one per applied event)</li>
 *   <li>{@code triage#<eventId>} — written by the triage workflow, not this store</li>
 * </ul>
 *
 * <p>Marker put (conditional on absence) and stats update run in one transaction,
 * so a crash between the two cannot desynchronize them and a duplicate delivery
 * cancels cleanly on the marker condition.
 */
public class DynamoStatsStore implements StatsStore {

    static final String STATS_PK = "stats";

    private final DynamoDbClient client;
    private final String tableName;

    public DynamoStatsStore(DynamoDbClient client, String tableName) {
        this.client = client;
        this.tableName = tableName;
    }

    @Override
    public boolean apply(FeedbackSubmittedEvent event) {
        try {
            client.transactWriteItems(TransactWriteItemsRequest.builder()
                    .transactItems(
                            TransactWriteItem.builder().put(Put.builder()
                                    .tableName(tableName)
                                    .item(Map.of(
                                            "pk", AttributeValue.fromS("evt#" + event.eventId),
                                            "feedbackId", AttributeValue.fromN(
                                                    Long.toString(event.feedbackId)),
                                            "userId", AttributeValue.fromS(event.userId),
                                            "rating", AttributeValue.fromN(
                                                    Integer.toString(event.rating))))
                                    .conditionExpression("attribute_not_exists(pk)")
                                    .build()).build(),
                            TransactWriteItem.builder().update(Update.builder()
                                    .tableName(tableName)
                                    .key(Map.of("pk", AttributeValue.fromS(STATS_PK)))
                                    .updateExpression("ADD cnt :one, ratingSum :rating")
                                    .expressionAttributeValues(Map.of(
                                            ":one", AttributeValue.fromN("1"),
                                            ":rating", AttributeValue.fromN(
                                                    Integer.toString(event.rating))))
                                    .build()).build())
                    .build());
            return true;
        } catch (TransactionCanceledException e) {
            boolean duplicate = e.hasCancellationReasons()
                    && "ConditionalCheckFailed".equals(e.cancellationReasons().get(0).code());
            if (duplicate) {
                return false;
            }
            throw e;
        }
    }
}
