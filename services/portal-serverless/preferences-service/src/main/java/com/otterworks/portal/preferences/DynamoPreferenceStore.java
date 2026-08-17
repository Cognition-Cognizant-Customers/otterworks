package com.otterworks.portal.preferences;

import java.util.Map;
import java.util.Optional;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.GetItemRequest;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;

/** DynamoDB persistence for user preferences. Partition key: {@code userId} (S). */
public class DynamoPreferenceStore implements PreferenceStore {

    private final DynamoDbClient client;
    private final String tableName;

    public DynamoPreferenceStore(DynamoDbClient client, String tableName) {
        this.client = client;
        this.tableName = tableName;
    }

    @Override
    public Optional<UserPreference> find(String userId) {
        var response = client.getItem(GetItemRequest.builder()
                .tableName(tableName)
                .key(Map.of("userId", AttributeValue.fromS(userId)))
                .build());
        if (!response.hasItem() || response.item().isEmpty()) {
            return Optional.empty();
        }
        var item = response.item();
        return Optional.of(new UserPreference(
                item.get("userId").s(),
                item.get("theme").s(),
                item.get("locale").s(),
                item.get("emailNotifications").bool()));
    }

    @Override
    public void put(UserPreference p) {
        client.putItem(PutItemRequest.builder()
                .tableName(tableName)
                .item(Map.of(
                        "userId", AttributeValue.fromS(p.getUserId()),
                        "theme", AttributeValue.fromS(p.getTheme()),
                        "locale", AttributeValue.fromS(p.getLocale()),
                        "emailNotifications", AttributeValue.fromBool(p.isEmailNotifications())))
                .build());
    }
}
