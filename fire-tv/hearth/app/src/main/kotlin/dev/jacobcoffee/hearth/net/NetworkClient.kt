package dev.jacobcoffee.hearth.net

import okhttp3.Credentials
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/** One shared client for every network call Hearth makes: widgets, catalog, theme downloads. */
object NetworkClient {
    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .writeTimeout(5, TimeUnit.SECONDS)
        .callTimeout(5, TimeUnit.SECONDS)
        // Must stay on: servers close idle keep-alive connections (Open-Meteo does), and the next
        // request on a pooled one fails with "unexpected end of stream" unless OkHttp retries it on
        // a fresh connection. Every call here is a GET from [get], so a retry is safe.
        .retryOnConnectionFailure(true)
        .build()

    fun get(url: String, basicAuthUser: String? = null, basicAuthPass: String? = null): Request {
        val builder = Request.Builder().url(url).get()
        if (basicAuthUser != null && basicAuthPass != null) {
            builder.header("Authorization", Credentials.basic(basicAuthUser, basicAuthPass))
        }
        return builder.build()
    }
}
