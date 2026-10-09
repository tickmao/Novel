package io.legado.server

import java.io.ByteArrayOutputStream
import java.net.URI
import java.net.http.HttpRequest
import java.nio.ByteBuffer
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Flow
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.*
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class NovelCompatibilityTest {
    @Test
    fun `spaced request options preserve the declared query encoding`() {
        val urls = mutableListOf<String>()
        val runner = RuleRunner { url ->
            urls.add(url)
            """{"books":[{"name":"Example","url":"/book/1"}]}"""
        }
        val source = buildJsonObject {
            put("bookSourceUrl", "https://fixture.example")
            put("searchUrl", "/search?q={{key}}, {\"charset\":\"GBK\"}")
            putJsonObject("ruleSearch") {
                put("bookList", "$.books")
                put("name", "$.name")
                put("bookUrl", "$.url")
            }
        }
        assertEquals(1, runner.search(source.toString(), "\u6D4B\u8BD5").size)
        assertEquals(listOf("https://fixture.example/search?q=%B2%E2%CA%D4"), urls)
    }

    @Test
    fun `book metadata produces distinct search result addresses`() {
        val runner = RuleRunner { _ ->
            """{"books":[{"name":"One","id":"1"},{"name":"Two","id":"2"}]}"""
        }
        val source = buildJsonObject {
            put("bookSourceUrl", "https://fixture.example")
            put("searchUrl", "/search?q={{key}}")
            putJsonObject("ruleSearch") {
                put("bookList", "$.books")
                put("name", "$.name")
                put("kind", "$.id")
                put("bookUrl", "/book/{{book.kind}}")
            }
        }
        assertEquals(listOf("https://fixture.example/book/1", "https://fixture.example/book/2"),
                     runner.search(source.toString(), "Example").map { it.bookUrl })
    }

    @Test
    fun `JavaScript source headers are evaluated before parsing`() {
        val source = buildJsonObject {
            put("bookSourceUrl", "https://fixture.example")
            put("header", "@js:JSON.stringify({'User-Agent':'Fixture/2','Referer':baseUrl})")
        }
        val method = RuleRunner::class.java.declaredMethods.single { it.name == "parseSourceHeaders" }
        method.isAccessible = true
        val headers = method.invoke(RuleRunner(), source, "https://fixture.example") as Map<*, *>
        assertEquals("Fixture/2", headers["User-Agent"])
        assertEquals("https://fixture.example", headers["Referer"])
    }

    @Test
    fun `source headers replace defaults without overriding transport framing`() {
        val request = request("""{"headers":{"user-agent":"Fixture/1","Connection":"keep-alive","Host":"127.0.0.1","Content-Length":"999"}}""")
        assertEquals(listOf("Fixture/1"), request.headers().allValues("User-Agent"))
        for (name in listOf("Connection", "Host", "Content-Length")) {
            assertFalse(request.headers().firstValue(name).isPresent)
        }
    }

    @Test
    fun `POST forms use the declared charset and a form content type`() {
        val request = request("""{"method":"POST","body":"q={{key}}","charset":"GBK"}""")
        assertEquals("application/x-www-form-urlencoded", request.headers().firstValue("Content-Type").get())
        assertEquals("q=%B2%E2%CA%D4", body(request))
    }

    @Test
    fun `an explicit JSON content type is retained`() {
        val request = request("""{"method":"POST","body":"{}","headers":{"content-type":"application/json"}}""")
        assertEquals(listOf("application/json"), request.headers().allValues("Content-Type"))
        assertEquals("{}", body(request))
    }

    private fun request(options: String): HttpRequest {
        val runner = RuleRunner()
        val split = RuleRunner::class.java.declaredMethods.single { it.name == "splitUrlOptions" }
        split.isAccessible = true
        val (_, parsed) = split.invoke(runner, "https://8.8.8.8/search, $options") as Pair<*, *>
        val build = RuleRunner::class.java.declaredMethods.single { it.name == "buildRequest" }
        build.isAccessible = true
        return build.invoke(runner, URI("https://8.8.8.8/search"), parsed, "\u6D4B\u8BD5") as HttpRequest
    }

    private fun body(request: HttpRequest): String {
        val bytes = ByteArrayOutputStream()
        val result = CompletableFuture<String>()
        request.bodyPublisher().get().subscribe(object : Flow.Subscriber<ByteBuffer> {
            override fun onSubscribe(subscription: Flow.Subscription) = subscription.request(Long.MAX_VALUE)
            override fun onNext(buffer: ByteBuffer) {
                val chunk = ByteArray(buffer.remaining())
                buffer.get(chunk)
                bytes.write(chunk)
            }
            override fun onError(error: Throwable) { result.completeExceptionally(error) }
            override fun onComplete() { result.complete(bytes.toString("UTF-8")) }
        })
        return result.get(2, TimeUnit.SECONDS)
    }
}
