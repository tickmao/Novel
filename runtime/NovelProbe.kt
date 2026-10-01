package io.legado.server

import java.io.PrintStream
import java.nio.file.Files
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*

/** Run one exact source revision in a fresh process, without reader caches. */
fun main() {
    val output = System.out
    System.setOut(PrintStream(System.err))
    val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    val first = readlnOrNull() ?: return
    val init = json.parseToJsonElement(first).jsonObject
    val source = init.getValue("source").toString()
    val fixtures = init["fixtures"]?.jsonObject
    val directory = Files.createTempDirectory("novel-probe-")
    val database = Database(directory.resolve("state.sqlite").toString())
    database.initialize("novel-ephemeral-fixture-password")
    val runner = if (fixtures != null) RuleRunner({ url ->
        fixtures[url]?.jsonPrimitive?.content ?: error("Missing fixture response")
    }, database) else RuleRunner(database)
    output.println(buildJsonObject {
        put("ok", true)
        put("engine_commit", "3cb7acb2a2892e71184c1dd12c8e8d7b5a60fbb7")
        put("protocol", 1)
    })
    output.flush()
    try {
        while (true) {
            val line = readlnOrNull() ?: break
            val request = json.parseToJsonElement(line).jsonObject
            fun arg(name: String) = request[name]?.jsonPrimitive?.content.orEmpty()
            val response = try {
                val value = when (arg("op")) {
                    "categories" -> {
                        val definition = json.parseToJsonElement(source).jsonObject
                        val code = definition["exploreUrl"]?.jsonPrimitive?.content.orEmpty()
                            .removePrefix("@js:").removePrefix("<js>").removeSuffix("</js>")
                        val context = JsExecutionContext(
                            sourceId = definition["bookSourceUrl"]?.jsonPrimitive?.content,
                            database = database,
                            jsLib = definition["jsLib"]?.jsonPrimitive?.content,
                        )
                        JsonPrimitive(runner.jsSandbox.eval(code, execContext = context)
                            ?: error("Discovery categories could not be evaluated"))
                    }
                    "search" -> json.encodeToJsonElement(runner.search(source, arg("keyword")))
                    "book" -> json.encodeToJsonElement(runner.details(source, arg("url")))
                    "toc" -> json.encodeToJsonElement(runner.chapters(source, arg("url")))
                    "content" -> json.encodeToJsonElement(runner.content(source, arg("url"), arg("book_name")))
                    else -> error("Unknown probe operation")
                }
                buildJsonObject { put("ok", true); put("value", value) }
            } catch (error: Exception) {
                buildJsonObject {
                    put("ok", false)
                    put("error_type", error.javaClass.simpleName)
                    put("error", error.message.orEmpty().take(300))
                }
            }
            output.println(response)
            output.flush()
        }
    } finally {
        database.close()
        directory.toFile().deleteRecursively()
    }
}
