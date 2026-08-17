package com.otterworks.portal.moderation;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestHandler;
import com.amazonaws.services.lambda.runtime.events.SQSEvent;
import com.amazonaws.services.lambda.runtime.events.SQSBatchResponse;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;

public final class Handler implements RequestHandler<SQSEvent, SQSBatchResponse> {

    private final ModerationConsumer consumer;

    public Handler() {
        this(new ModerationConsumer(
                new DynamoModerationStore(
                        software.amazon.awssdk.services.dynamodb.DynamoDbClient.create(),
                        System.getenv("MODERATION_TABLE_NAME")),
                new ObjectMapper()));
    }

    Handler(ModerationConsumer consumer) {
        this.consumer = consumer;
    }

    @Override
    public SQSBatchResponse handleRequest(SQSEvent event, Context context) {
        var failures = new ArrayList<SQSBatchResponse.BatchItemFailure>();
        for (SQSEvent.SQSMessage message : event.getRecords()) {
            try {
                consumer.process(message.getBody());
            } catch (RuntimeException failure) {
                failures.add(new SQSBatchResponse.BatchItemFailure(message.getMessageId()));
            }
        }
        return new SQSBatchResponse(failures);
    }
}
