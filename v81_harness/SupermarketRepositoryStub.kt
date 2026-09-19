package com.example.smartcampania

internal object SupermarketRepository {
    data class Product(
        val key: String,
        val supermarket: String,
        val store: String?,
        val name: String,
        val brand: String?,
        val category: String?,
        val quantityValue: Double?,
        val quantityUnit: String?,
        val priceEur: Double,
        val unitPriceEur: Double?,
        val unitPriceUnit: String?,
        val variableWeight: Boolean,
        val sourceUrl: String?,
        val checkedAt: String?
    )
}