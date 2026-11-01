package dev.jacobcoffee.hearth.catalog

import kotlinx.serialization.Serializable

@Serializable
data class CatalogIndex(
    val schema: Int,
    val themes: List<CatalogThemeEntry>,
)

@Serializable
data class CatalogThemeEntry(
    val id: String,
    val name: String,
    val version: Int,
    val url: String,
    val sha256: String,
    val size: Long,
    val preview: String? = null,
)
