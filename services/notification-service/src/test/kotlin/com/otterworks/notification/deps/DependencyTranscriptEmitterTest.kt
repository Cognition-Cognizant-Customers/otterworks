package com.otterworks.notification.deps

import com.otterworks.notification.template.NotificationTemplates
import java.io.File
import kotlin.test.Test
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Emits the observed interpolation transcript for this module.
 *
 * Driven by the dependency harness (security/deps): without -Dow.deps.cases and
 * -Dow.deps.observed the test records nothing and passes, so a plain `gradle test`
 * is unaffected. Grading against the recorded expectations belongs to the harness,
 * so that one comparator governs every module regardless of language.
 */
class DependencyTranscriptEmitterTest {

    @Test
    fun emitTranscript() {
        val casesPath = System.getProperty("ow.deps.cases") ?: return
        val observedPath = System.getProperty("ow.deps.observed") ?: return

        val spec = Json.parseToJsonElement(File(casesPath).readText()).jsonObject
        val records = spec.getValue("cases").jsonArray.map { element ->
            val testCase = element.jsonObject
            val record = linkedMapOf<String, JsonPrimitive>(
                "id" to JsonPrimitive(testCase.getValue("id").jsonPrimitive.content),
            )
            try {
                val value = render(testCase)
                record["outcome"] = JsonPrimitive("ok")
                record["value"] = JsonPrimitive(value)
            } catch (failure: Throwable) {
                record["outcome"] = JsonPrimitive("error")
                record["error_type"] = JsonPrimitive(failure.javaClass.name)
                record["error_message"] = JsonPrimitive(failure.message.toString())
            }
            JsonObject(record)
        }

        val out = JsonObject(
            mapOf(
                "module" to spec.getValue("module").jsonPrimitive,
                "cases" to JsonArray(records),
            ),
        )

        val outputFile = File(observedPath)
        outputFile.parentFile?.mkdirs()
        outputFile.writeText(Json { prettyPrint = true }.encodeToString(JsonObject.serializer(), out))
    }

    private fun render(testCase: JsonObject): String {
        val template = withFixture(testCase, testCase.getValue("template").jsonPrimitive.content)
        return when (val kind = testCase.getValue("kind").jsonPrimitive.content) {
            "notification" -> NotificationTemplates.replaceVariables(template, variables(testCase))
            "configured" -> NotificationTemplates.resolveOperatorString(template)
            else -> throw IllegalArgumentException("unsupported case kind: $kind")
        }
    }

    /**
     * Materialise a local file for cases that probe file-reading lookups, so the
     * recorded template stays machine independent.
     */
    private fun withFixture(testCase: JsonObject, template: String): String {
        val content = testCase["fixture_content"]?.jsonPrimitive?.contentOrNull ?: return template
        val fixture = File.createTempFile("ow-deps-fixture", ".txt")
        fixture.deleteOnExit()
        fixture.writeText(content)
        return template
            .replace("@FIXTURE_URL@", fixture.toURI().toString())
            .replace("@FIXTURE_PATH@", fixture.absolutePath)
    }

    private fun variables(testCase: JsonObject): Map<String, String> =
        testCase["vars"]?.jsonObject?.mapValues { it.value.jsonPrimitive.content } ?: emptyMap()
}
