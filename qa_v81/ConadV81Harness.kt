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

private fun loadProducts(path: String, supermarketName: String, storeName: String?): List<SupermarketRepository.Product> {
    val lines = File(path).readLines(Charsets.UTF_8)
    require(lines.isNotEmpty()) { "products TSV vuoto" }
    return lines.drop(1).filter { it.isNotBlank() }.map { line ->
        val c = line.split('\t')
        require(c.size >= 12) { "riga prodotti invalida: $line" }
        SupermarketRepository.Product(
            key = c[0],
            supermarket = supermarketName,
            store = storeName,
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
    require(args.size >= 2) { "uso: products.tsv recipes.tsv [persons] [supermarket] [store]" }
    val persons = args.getOrNull(2)?.toIntOrNull() ?: 2
    val supermarketName = args.getOrNull(3)?.takeIf { it.isNotBlank() } ?: "Conad"
    val storeName = args.getOrNull(4)?.takeIf { it.isNotBlank() }
    val products = loadProducts(args[0], supermarketName, storeName)
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
    println("supermarket=$supermarketName")
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
    // Lidl €50 feasibility: build a real 7-day basket with 7 unique primi,
    // 7 unique secondi and 7 unique contorni. Cost is recalculated on the
    // aggregate ingredient demand, so shared packs/leftovers are counted once.
    if (supermarketName.lowercase().contains("lidl")) {
        val completeByRole = mapOf(
            "primo" to complete.filter { (r, _) -> r.category == "primo" || r.roles.split(',').any { it == "primo" } }.map { it.first },
            "secondo" to complete.filter { (r, _) -> r.category == "secondo" || r.roles.split(',').any { it == "secondo" } }.map { it.first },
            "contorno" to complete.filter { (r, _) -> r.category == "contorno" || r.roles.split(',').any { it == "contorno" } }.map { it.first }
        )
        fun basketCalc(rs: List<HarnessRecipe>): ShoppingCalculation =
            ShoppingCalculator.calculate(rs.flatMap { it.ingredients }, products)
        fun recipeText(r: HarnessRecipe): String =
            IngredientMatcher.normalize(r.name + " " + r.ingredients.joinToString(" ") { it.name })
        fun family(r: HarnessRecipe): String {
            val s = recipeText(r)
            return when {
                s.contains("uova") || s.contains("uovo") -> "uova"
                s.contains("fagiol") -> "fagioli"
                s.contains("ceci") -> "ceci"
                s.contains("lenticch") -> "lenticchie"
                s.contains("patat") -> "patate"
                s.contains("zucchin") -> "zucchine"
                s.contains("melanzan") -> "melanzane"
                s.contains("riso") -> "riso"
                else -> r.id
            }
        }
        fun hasTerms(r: HarnessRecipe, terms: List<String>): Boolean {
            val s = recipeText(r)
            return terms.any { s.contains(it) }
        }
        val fishTerms = listOf("vongol","cozz","gamber","calamar","seppi","polpo","baccala","merluzz","tonno","sgombr","alici","acciugh","salmone","orata","spigola","pesce","scampi")
        val meatTerms = listOf("pollo","manzo","vitello","maiale","salsic","coniglio","agnello","bovino","carne","guancial","pancetta")
        val legumeTerms = listOf("fagiol","ceci","lenticch","pisell")

        // Seed the weekly search with the semantic requirements that the real app needs.
        // Each seed is chosen by the lowest resulting aggregate basket cost, not by standalone recipe price.
        val selected = mutableListOf<HarnessRecipe>()
        fun addCheapest(role: String, predicate: (HarnessRecipe) -> Boolean): Boolean {
            val best = completeByRole[role].orEmpty()
                .filter { predicate(it) && selected.none { s -> s.id == it.id } }
                .mapNotNull { cand ->
                    val fam = family(cand)
                    if (fam in setOf("uova","fagioli","ceci","lenticchie","patate") && selected.count { family(it) == fam } >= 1) null
                    else {
                        val calc = basketCalc(selected + cand)
                        if (calc.unresolved.isEmpty()) cand to calc.totalCost else null
                    }
                }.minByOrNull { it.second }
            if (best != null) selected += best.first
            return best != null
        }
        val seededFishPrimo = addCheapest("primo") { hasTerms(it, fishTerms) }
        val seededMeatPrimo = addCheapest("primo") { hasTerms(it, meatTerms) }
        val seededLegume = addCheapest("primo") { hasTerms(it, legumeTerms) }
        if (!seededLegume) addCheapest("secondo") { hasTerms(it, legumeTerms) }
        if (!seededFishPrimo) addCheapest("secondo") { hasTerms(it, fishTerms) }
        if (!seededMeatPrimo) addCheapest("secondo") { hasTerms(it, meatTerms) }

        val target = mapOf("primo" to 7, "secondo" to 7, "contorno" to 7)
        for (role in listOf("primo","secondo","contorno")) {
            while (selected.count { it.category == role || it.roles.split(',').any { x -> x == role } } < target.getValue(role)) {
                if (!addCheapest(role) { true }) break
            }
        }
        // Multi-start constrained search. The old single-swap descent could get
        // trapped in a local minimum. Start from several semantic seed combinations,
        // fill all 21 slots by aggregate basket cost, then apply local descent and
        // retain the cheapest fully valid weekly basket.
        fun roleOf(r: HarnessRecipe): String = when {
            r.category == "primo" || r.roles.split(',').any { it == "primo" } -> "primo"
            r.category == "secondo" || r.roles.split(',').any { it == "secondo" } -> "secondo"
            else -> "contorno"
        }
        fun constraintsOk(rs: List<HarnessRecipe>): Boolean {
            if (rs.map { it.id }.distinct().size != 21) return false
            if (listOf("primo","secondo","contorno").any { role -> rs.count { roleOf(it) == role } != 7 }) return false
            val fams = rs.map { family(it) }
            if (listOf("uova","fagioli","ceci","lenticchie","patate").any { fam -> fams.count { it == fam } > 1 }) return false
            val corpus = rs.joinToString(" ") { recipeText(it) }
            val primi = rs.filter { roleOf(it) == "primo" }
            return legumeTerms.any { corpus.contains(it) } &&
                fishTerms.any { corpus.contains(it) } &&
                meatTerms.any { corpus.contains(it) } &&
                primi.any { r -> hasTerms(r, fishTerms) } &&
                primi.any { r -> hasTerms(r, meatTerms) }
        }
        fun familyAllowed(rs: List<HarnessRecipe>, cand: HarnessRecipe): Boolean {
            val fam = family(cand)
            return fam !in setOf("uova","fagioli","ceci","lenticchie","patate") || rs.none { family(it) == fam }
        }
        fun descend(seed: List<HarnessRecipe>): MutableList<HarnessRecipe> {
            val out = seed.toMutableList()
            var changed = true
            while (changed) {
                changed = false
                var bestCost = basketCalc(out).totalCost
                var bestSwap: Pair<Int,HarnessRecipe>? = null
                for (i in out.indices) {
                    val role = roleOf(out[i])
                    for (cand in completeByRole[role].orEmpty()) {
                        if (out.any { it.id == cand.id }) continue
                        val trial = out.toMutableList().also { it[i] = cand }
                        if (!constraintsOk(trial)) continue
                        val calc = basketCalc(trial)
                        if (calc.unresolved.isEmpty() && calc.totalCost + 0.0001 < bestCost) {
                            bestCost = calc.totalCost
                            bestSwap = i to cand
                        }
                    }
                }
                if (bestSwap != null) {
                    out[bestSwap.first] = bestSwap.second
                    changed = true
                }
            }
            return out
        }
        fun cheapestCandidates(role: String, terms: List<String>, limit: Int): List<HarnessRecipe> =
            completeByRole[role].orEmpty().filter { hasTerms(it, terms) }
                .mapNotNull { r -> basketCalc(listOf(r)).let { if (it.unresolved.isEmpty()) r to it.totalCost else null } }
                .sortedBy { it.second }.take(limit).map { it.first }

        val fishPrimi = cheapestCandidates("primo", fishTerms, 5)
        val meatPrimi = cheapestCandidates("primo", meatTerms, 5)
        val legumeAny = (cheapestCandidates("primo", legumeTerms, 6) + cheapestCandidates("secondo", legumeTerms, 4))
            .distinctBy { it.id }
        var bestWeek: MutableList<HarnessRecipe>? = null
        var bestWeekCost = Double.POSITIVE_INFINITY
        for (fp in fishPrimi) for (mp in meatPrimi) for (lp in legumeAny) {
            val trial = mutableListOf(fp,mp,lp).distinctBy { it.id }.toMutableList()
            if (trial.size < 3 || !trial.all { familyAllowed(trial.filter { x -> x.id != it.id }, it) }) continue
            var valid = true
            for (role in listOf("primo","secondo","contorno")) {
                while (trial.count { roleOf(it) == role } < 7) {
                    val cand = completeByRole[role].orEmpty()
                        .filter { r -> trial.none { it.id == r.id } && familyAllowed(trial,r) }
                        .mapNotNull { r ->
                            val calc = basketCalc(trial + r)
                            if (calc.unresolved.isEmpty()) r to calc.totalCost else null
                        }.minByOrNull { it.second }?.first
                    if (cand == null) { valid = false; break } else trial += cand
                }
                if (!valid) break
            }
            if (!valid || trial.size != 21 || !constraintsOk(trial)) continue
            val optimized = descend(trial)
            val cost = basketCalc(optimized).totalCost
            if (cost < bestWeekCost) {
                bestWeekCost = cost
                bestWeek = optimized
            }
        }
        if (bestWeek != null) {
            selected.clear()
            selected.addAll(bestWeek!!)
        }
        println("eur50_optimized=true")
        val basket = basketCalc(selected)
        // Count each selected recipe in exactly one weekly slot. A recipe may advertise
        // multiple menu_roles, but its primary category is the slot used by this QA.
        val counts = mapOf(
            "primo" to selected.count { roleOf(it) == "primo" },
            "secondo" to selected.count { roleOf(it) == "secondo" },
            "contorno" to selected.count { roleOf(it) == "contorno" }
        )
        val familyNames = selected.map {
            val s = IngredientMatcher.normalize(it.name + " " + it.ingredients.joinToString(" ") { x -> x.name })
            when {
                s.contains("uova") || s.contains("uovo") -> "uova"
                s.contains("fagiol") -> "fagioli"
                s.contains("ceci") -> "ceci"
                s.contains("lenticch") -> "lenticchie"
                s.contains("patat") -> "patate"
                s.contains("zucchin") -> "zucchine"
                s.contains("melanzan") -> "melanzane"
                s.contains("riso") -> "riso"
                else -> it.id
            }
        }
        val varietyOk = listOf("uova","fagioli","ceci","lenticchie","patate").all { fam -> familyNames.count { it == fam } <= 1 }
        val feasible = counts.values.all { it >= 7 } && basket.unresolved.isEmpty() && basket.totalCost <= 50.0 && varietyOk
        println("--- LIDL_EUR50_FEASIBILITY ---")
        println("eur50_persons=$persons")
        println("eur50_days=7")
        println("eur50_primi=${counts["primo"]}")
        println("eur50_secondi=${counts["secondo"]}")
        println("eur50_contorni=${counts["contorno"]}")
        println("eur50_unique_recipes=${selected.map { it.id }.distinct().size}")
        println("eur50_unresolved=${basket.unresolved.size}")
        println("eur50_total=%.4f".format(Locale.US, basket.totalCost))
        val corpus = selected.joinToString(" ") { r -> IngredientMatcher.normalize(r.name + " " + r.ingredients.joinToString(" ") { it.name }) }
        fun hasAny(vararg terms: String): Boolean = terms.any { corpus.contains(it) }
        val hasLegumes = hasAny("fagiol", "ceci", "lenticch", "pisell")
        val hasFish = hasAny("vongol", "cozz", "gamber", "calamar", "seppi", "polpo", "baccala", "merluzz", "tonno", "sgombr", "alici", "acciugh", "salmone", "orata", "spigola", "pesce", "scampi")
        val hasMeat = hasAny("pollo", "manzo", "vitello", "maiale", "salsic", "coniglio", "agnello", "bovino", "carne", "guancial", "pancetta")
        val primoCorpus = selected.filter { it.category == "primo" || it.roles.split(',').any { x -> x == "primo" } }
            .joinToString(" ") { r -> IngredientMatcher.normalize(r.name + " " + r.ingredients.joinToString(" ") { it.name }) }
        fun primoHasAny(vararg terms: String): Boolean = terms.any { primoCorpus.contains(it) }
        val hasFishPrimo = primoHasAny("vongol", "cozz", "gamber", "calamar", "seppi", "polpo", "baccala", "merluzz", "tonno", "sgombr", "alici", "acciugh", "salmone", "orata", "spigola", "pesce", "scampi")
        val hasMeatPrimo = primoHasAny("pollo", "manzo", "vitello", "maiale", "salsic", "coniglio", "agnello", "bovino", "carne", "guancial", "pancetta")
        val semanticVarietyOk = varietyOk && hasLegumes && hasFish && hasMeat
        println("eur50_variety_ok=$varietyOk")
        println("eur50_has_legumes=$hasLegumes")
        println("eur50_has_fish=$hasFish")
        println("eur50_has_meat=$hasMeat")
        println("eur50_has_fish_primo=$hasFishPrimo")
        println("eur50_has_meat_primo=$hasMeatPrimo")
        println("eur50_semantic_variety_ok=$semanticVarietyOk")
        println("eur50_feasible=$feasible")
        selected.forEach { println("EUR50_RECIPE\\t${it.category}\\t${it.id}\\t${it.name}") }
        basket.lines.sortedBy { it.ingredientName }.forEach {
            println("EUR50_CART\\t${it.ingredientName}\\t${it.product.name}\\t${it.packs}\\t${"%.4f".format(Locale.US, it.totalCost)}")
        }
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
