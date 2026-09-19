package com.example.smartcampania

import java.io.File
import java.util.Locale

internal data class HarnessRecipe(
    val id: String,
    val name: String,
    val category: String,
    val roles: String,
    val pricingStatus: String,
    val ingredients: MutableList<IngredientDemand> = mutableListOf()
)

private fun d(s: String): Double? = s.trim().takeIf { it.isNotEmpty() }?.toDoubleOrNull()
private fun unescape(s: String): String = s.replace("\\n", " ").replace("\\t", " ").trim()

private fun loadProducts(path: String): List<SupermarketRepository.Product> {
    val lines = File(path).readLines(Charsets.UTF_8)
    require(lines.isNotEmpty()) { "products TSV vuoto" }
    return lines.drop(1).filter { it.isNotBlank() }.map { line ->
        val c = line.split('\t')
        require(c.size >= 12) { "riga prodotti invalida: $line" }
        SupermarketRepository.Product(
            key = c[0],
            supermarket = "Conad",
            store = "010548",
            name = unescape(c[1]),
            brand = c[2].ifBlank { null },
            category = c[3].ifBlank { null },
            quantityValue = d(c[4]),
            quantityUnit = c[5].ifBlank { null },
            priceEur = d(c[6]) ?: error("prezzo mancante ${c[0]}"),
            unitPriceEur = d(c[7]),
            unitPriceUnit = when (c[8].trim().uppercase()) {
                "EUR/KG", "KG" -> "kg"
                "EUR/L", "L", "LT" -> "litro"
                "EUR/PZ", "PZ", "PEZZO" -> "pezzo"
                else -> c[8].ifBlank { null }
            },
            variableWeight = c[9] == "1" || c[9].equals("true", true),
            sourceUrl = c.getOrNull(10)?.ifBlank { null },
            checkedAt = c.getOrNull(11)?.ifBlank { null }
        )
    }
}

private fun loadRecipes(path: String, persons: Int): List<HarnessRecipe> {
    val lines = File(path).readLines(Charsets.UTF_8)
    require(lines.isNotEmpty()) { "recipes TSV vuoto" }
    val map = linkedMapOf<String, HarnessRecipe>()
    for (line in lines.drop(1)) {
        if (line.isBlank()) continue
        val c = line.split('\t')
        require(c.size >= 8) { "riga ricetta invalida: $line" }
        val r = map.getOrPut(c[0]) {
            HarnessRecipe(c[0], c[1], c[2], c[3], c[4])
        }
        if (c[5].isNotBlank()) {
            val perPerson = d(c[6]) ?: 0.0
            r.ingredients += IngredientDemand(
                name = c[5],
                quantity = perPerson * persons,
                unit = c[7],
                recipeNames = setOf(c[1])
            )
        }
    }
    return map.values.toList()
}

fun main(args: Array<String>) {
    require(args.size >= 2) { "uso: products.tsv recipes.tsv [persons]" }
    val persons = args.getOrNull(2)?.toIntOrNull() ?: 2
    val products = loadProducts(args[0])
    val recipes = loadRecipes(args[1], persons)
    IngredientMatcher.prepare(products)

    val testable = recipes.filter { it.pricingStatus != "BLOCKED_UNTIL_SELECTED_SUPERMARKET_MATCH" }
    val complete = mutableListOf<Pair<HarnessRecipe, ShoppingCalculation>>()
    val failed = mutableListOf<Pair<HarnessRecipe, ShoppingCalculation>>()
    val unresolvedCount = linkedMapOf<String, Int>()
    val unresolvedReason = linkedMapOf<String, MutableMap<String, Int>>()
    val unresolvedRecipes = linkedMapOf<String, MutableSet<String>>()

    for (r in testable) {
        val calc = ShoppingCalculator.calculate(r.ingredients, products)
        if (calc.unresolved.isEmpty()) complete += r to calc else failed += r to calc
        for (u in calc.unresolved) {
            val key = IngredientMatcher.normalize(u.ingredientName)
            unresolvedCount[key] = (unresolvedCount[key] ?: 0) + 1
            val rm = unresolvedReason.getOrPut(key) { linkedMapOf() }
            rm[u.reason] = (rm[u.reason] ?: 0) + 1
            unresolvedRecipes.getOrPut(key) { linkedSetOf() } += r.name
        }
    }

    fun roleCount(role: String): Int = complete.count { (r, _) ->
        r.category == role || r.roles.split(',').any { it == role }
    }

    println("V81_CONAD_REAL_KOTLIN_HARNESS")
    println("persons=$persons")
    println("products=${products.size}")
    println("recipes_total=${recipes.size}")
    println("recipes_testable=${testable.size}")
    println("recipes_complete=${complete.size}")
    println("recipes_failed=${failed.size}")
    println("primi_complete=${roleCount("primo")}")
    println("secondi_complete=${roleCount("secondo")}")
    println("contorni_complete=${roleCount("contorno")}")
    println("piatti_unici_complete=${roleCount("piatto_unico")}")
    println("dolci_complete=${roleCount("dolce")}")
    println("threshold_primi=${roleCount("primo") >= 14}")
    println("threshold_secondi=${roleCount("secondo") >= 14}")
    println("threshold_contorni=${roleCount("contorno") >= 7}")
    println("--- COMPLETE_RECIPES ---")
    for ((r, calc) in complete.sortedWith(compareBy({ it.first.category }, { it.first.name }))) {
        println("OK\t${r.category}\t${r.id}\t${r.name}\t%.4f".format(Locale.US, calc.totalCost))
    }
    println("--- UNRESOLVED_FREQUENCY ---")
    for ((ingredient, count) in unresolvedCount.entries.sortedByDescending { it.value }) {
        val reasons = unresolvedReason[ingredient].orEmpty().entries.joinToString(" | ") { "${it.key}=${it.value}" }
        val rs = unresolvedRecipes[ingredient].orEmpty().take(8).joinToString(" ; ")
        val loose = products.asSequence().filter {
            IngredientMatcher.normalize(it.name).contains(ingredient)
        }.take(5).joinToString(" || ") { "${it.name} [${it.category}] ${it.quantityValue ?: "?"}${it.quantityUnit ?: ""} €${it.priceEur}" }
        println("MISS\t$count\t$ingredient\t$reasons\t$loose\t$rs")
    }
    println("--- FAILED_RECIPES ---")
    for ((r, calc) in failed.sortedWith(compareBy({ it.first.category }, { it.first.name }))) {
        val miss = calc.unresolved.joinToString(" | ") { "${it.ingredientName}:${it.reason}" }
        println("FAIL\t${r.category}\t${r.id}\t${r.name}\t$miss")
    }
}
// QA_TRIGGER_AUTHORIZED_FIVE_REFERENCES_2026_09_19
