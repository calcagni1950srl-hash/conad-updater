package com.example.smartcampania

import kotlin.math.ceil

internal enum class QuantityFamily { MASS, VOLUME, PIECE, UNKNOWN }

internal data class NormalizedQuantity(
    val baseValue: Double,
    val family: QuantityFamily,
    val displayUnit: String
)

internal data class IngredientDemand(
    val name: String,
    val quantity: Double,
    val unit: String,
    val recipeNames: Set<String> = emptySet()
)

internal data class ShoppingLine(
    val ingredientName: String,
    val requiredQuantity: Double,
    val requiredUnit: String,
    val product: SupermarketRepository.Product,
    val packs: Int,
    val purchasedBaseQuantity: Double,
    val usedBaseQuantity: Double,
    val leftoverBaseQuantity: Double,
    val baseUnit: String,
    val totalCost: Double,
    val matchReason: String,
    val recipeNames: Set<String>
)

internal data class UnresolvedIngredient(
    val ingredientName: String,
    val quantity: Double?,
    val unit: String,
    val reason: String,
    val recipeNames: Set<String>
)

internal data class ShoppingCalculation(
    val lines: List<ShoppingLine>,
    val unresolved: List<UnresolvedIngredient>,
    val totalCost: Double
)

internal object QuantityParser {

    data class Parsed(val value: Double, val unit: String)

    fun parse(text: String?): Parsed? {
        if (text.isNullOrBlank()) return null
        val s = text.lowercase().replace(',', '.')

        val multi = Regex("(\\d+)\\s*[x×]\\s*(\\d+(?:\\.\\d+)?)\\s*(kg|gr|g|ml|cl|lt|l|pz|pezzi)")
            .find(s)
        if (multi != null) {
            val n = multi.groupValues[1].toDoubleOrNull() ?: return null
            val v = multi.groupValues[2].toDoubleOrNull() ?: return null
            return Parsed(n * v, canonicalUnit(multi.groupValues[3]))
        }

        val normal = Regex("(\\d+(?:\\.\\d+)?)\\s*(kg|gr|g|ml|cl|lt|l|pz|pezzi)\\b")
            .find(s)
        if (normal != null) {
            return Parsed(
                normal.groupValues[1].toDoubleOrNull() ?: return null,
                canonicalUnit(normal.groupValues[2])
            )
        }

        val reversed = Regex("\\b(kg|gr|g|ml|cl|lt|l|pz|pezzi)\\s*(\\d+(?:\\.\\d+)?)")
            .find(s)
        if (reversed != null) {
            return Parsed(
                reversed.groupValues[2].toDoubleOrNull() ?: return null,
                canonicalUnit(reversed.groupValues[1])
            )
        }

        return null
    }

    fun parseUnitPrice(text: String?): Pair<Double, String>? {
        if (text.isNullOrBlank()) return null
        val s = text.lowercase().replace(',', '.')
        val m = Regex("(\\d+(?:\\.\\d+)?)\\s*€?\\s*(?:/|al|per)?\\s*(kg|litro|l|lt|pezzo|pz)")
            .find(s) ?: return null
        val v = m.groupValues[1].toDoubleOrNull() ?: return null
        val u = when (m.groupValues[2]) {
            "l", "lt", "litro" -> "litro"
            "pz", "pezzo" -> "pezzo"
            else -> "kg"
        }
        return v to u
    }

    fun familyOf(unit: String?): QuantityFamily = when (canonicalUnit(unit.orEmpty())) {
        "g", "kg" -> QuantityFamily.MASS
        "ml", "l" -> QuantityFamily.VOLUME
        "pz" -> QuantityFamily.PIECE
        else -> QuantityFamily.UNKNOWN
    }

    fun normalizeDemand(value: Double, unit: String): NormalizedQuantity? =
        normalize(value, unit)

    fun normalizeProductQuantity(value: Double?, unit: String?): NormalizedQuantity? {
        if (value == null || unit.isNullOrBlank()) return null
        return normalize(value, unit)
    }

    fun normalizeProductQuantity(
        product: SupermarketRepository.Product,
        ingredientName: String
    ): NormalizedQuantity? {
        val ingredient = IngredientMatcher.normalize(ingredientName)

        // Piccolo a volte espone le uova con metadato card "1 pz" anche se
        // il prodotto commerciale è una confezione da 4/6/10/12 uova.
        // Per le uova usiamo quindi SOLO un conteggio esplicito nel nome.
        if (ingredient == "uova") {
            val direct = normalizeProductQuantity(product.quantityValue, product.quantityUnit)
            if (direct != null && direct.family == QuantityFamily.PIECE && direct.baseValue in 4.0..30.0) {
                return direct
            }

            val n = IngredientMatcher.normalize(product.name)
            val patterns = listOf(
                Regex("(?:x|da)\\s*(\\d{1,2})\\b", RegexOption.IGNORE_CASE),
                Regex("\\b(\\d{1,2})\\s*(?:uova|pz|pezzi)\\b", RegexOption.IGNORE_CASE)
            )
            val count = patterns.firstNotNullOfOrNull { r ->
                r.find(n)?.groupValues?.getOrNull(1)?.toDoubleOrNull()
            }
            if (count != null && count in 4.0..30.0) {
                return NormalizedQuantity(count, QuantityFamily.PIECE, "pz")
            }
            return null
        }

        return normalizeProductQuantity(product.quantityValue, product.quantityUnit)
    }

    private fun normalize(value: Double, unit: String): NormalizedQuantity? {
        if (value <= 0.0) return null
        return when (canonicalUnit(unit)) {
            "g" -> NormalizedQuantity(value, QuantityFamily.MASS, "g")
            "kg" -> NormalizedQuantity(value * 1000.0, QuantityFamily.MASS, "g")
            "ml" -> NormalizedQuantity(value, QuantityFamily.VOLUME, "ml")
            "cl" -> NormalizedQuantity(value * 10.0, QuantityFamily.VOLUME, "ml")
            "l" -> NormalizedQuantity(value * 1000.0, QuantityFamily.VOLUME, "ml")
            "pz" -> NormalizedQuantity(value, QuantityFamily.PIECE, "pz")
            else -> null
        }
    }

    fun canonicalUnit(unit: String): String = when (unit.trim().lowercase()) {
        "g", "gr", "grammo", "grammi" -> "g"
        "kg", "chilo", "chili" -> "kg"
        "ml", "millilitro", "millilitri" -> "ml"
        "cl" -> "cl"
        "l", "lt", "litro", "litri" -> "l"
        "pz", "pezzo", "pezzi", "uovo", "uova" -> "pz"
        else -> unit.trim().lowercase()
    }
}

internal object ShoppingCalculator {

    private data class Bucket(
        val canonicalName: String,
        val displayName: String,
        val quantified: MutableList<IngredientDemand> = mutableListOf(),
        val unquantified: MutableList<IngredientDemand> = mutableListOf(),
        val recipeNames: MutableSet<String> = linkedSetOf()
    )

    /**
     * V32 - ragionamento da vero carrello:
     *
     * 1) unisce lo stesso ingrediente anche se scritto in modi diversi;
     * 2) q.b., spicchi, cucchiai ecc. NON diventano righe separate;
     * 3) se dello stesso ingrediente esiste almeno una quantità misurabile,
     *    le quantità misurabili vengono sommate e le occorrenze q.b./cucchiai
     *    vengono considerate già coperte dalla stessa confezione;
     * 4) se esistono solo quantità culinarie non convertibili, compra UNA sola
     *    confezione reale domestica, senza inventare grammi;
     * 5) la frutta usata come portata generica viene mostrata una sola volta.
     */
    fun calculate(
        demands: List<IngredientDemand>,
        products: List<SupermarketRepository.Product>
    ): ShoppingCalculation {

        val buckets = buildBuckets(demands)

        val lines = mutableListOf<ShoppingLine>()
        val unresolved = mutableListOf<UnresolvedIngredient>()

        fun addOneRealPackageFallback(
            bucket: Bucket,
            requiredQuantity: Double,
            requiredUnit: String
        ): Boolean {
            // V43: ingredienti culinari espressi in pezzi (es. 1 calamaro,
            // 2 gamberi, mezza cipolla) possono essere venduti da Piccolo a
            // peso. Non inventiamo il peso del singolo pezzo: compriamo UNA
            // confezione/prodotto reale compatibile e usiamo il suo prezzo vero.
            if (requiredUnit != "pz" || bucket.canonicalName == "uova") return false

            val purchaseMatch = IngredientMatcher.bestPurchaseMatch(
                ingredientName = bucket.canonicalName,
                products = products
            ) ?: return false

            val pack = QuantityParser.normalizeProductQuantity(
                product = purchaseMatch.product,
                ingredientName = bucket.canonicalName
            )

            val variablePack = pack?.takeIf {
                purchaseMatch.product.variableWeight &&
                    it.family != QuantityFamily.UNKNOWN
            }
            val realCost = if (variablePack != null) {
                variableWeightCost(
                    product = purchaseMatch.product,
                    requiredBaseQuantity = variablePack.baseValue,
                    family = variablePack.family
                ) ?: return false
            } else {
                purchaseMatch.product.priceEur
            }

            lines += ShoppingLine(
                ingredientName = bucket.displayName,
                requiredQuantity = variablePack?.baseValue ?: requiredQuantity,
                requiredUnit = variablePack?.displayUnit ?: requiredUnit,
                product = purchaseMatch.product,
                packs = 1,
                purchasedBaseQuantity = pack?.baseValue ?: 0.0,
                usedBaseQuantity = variablePack?.baseValue ?: 0.0,
                leftoverBaseQuantity = 0.0,
                baseUnit = pack?.displayUnit.orEmpty(),
                totalCost = realCost,
                matchReason = purchaseMatch.reason + if (variablePack != null) {
                    " • acquisto reale a peso"
                } else {
                    " • 1 confezione reale"
                },
                recipeNames = bucket.recipeNames
            )
            return true
        }

        for (bucket in buckets.values) {

            // Caso A: almeno una quantità reale/convertibile.
            if (bucket.quantified.isNotEmpty()) {
                val byFamily = bucket.quantified.groupBy {
                    QuantityParser.familyOf(it.unit)
                }

                for ((family, familyDemands) in byFamily) {
                    if (family == QuantityFamily.UNKNOWN) continue

                    // V44: se la stessa verdura/frutta compare sia in grammi sia
                    // in pezzi nella settimana, non compriamo una seconda
                    // confezione solo per la forma "1 cipolla / 1 pomodoro".
                    // La quantità in grammi rimane quella misurabile e verificabile.
                    if (
                        family == QuantityFamily.PIECE &&
                        byFamily.containsKey(QuantityFamily.MASS) &&
                        bucket.canonicalName in setOf(
                            "cipolla", "pomodoro", "pomodorini", "peperoni",
                            "zucchine", "melanzane", "carciofi", "limone"
                        )
                    ) {
                        continue
                    }

                    val totalBase = familyDemands.sumOf { demand ->
                        QuantityParser.normalizeDemand(
                            demand.quantity,
                            demand.unit
                        )?.baseValue ?: 0.0
                    }

                    if (totalBase <= 0.0) continue

                    val displayUnit = when (family) {
                        QuantityFamily.MASS -> "g"
                        QuantityFamily.VOLUME -> "ml"
                        QuantityFamily.PIECE -> "pz"
                        QuantityFamily.UNKNOWN -> ""
                    }

                    val mergedDemand = IngredientDemand(
                        name = bucket.canonicalName,
                        quantity = totalBase,
                        unit = displayUnit,
                        recipeNames = bucket.recipeNames
                    )

                    val match = IngredientMatcher.bestMatch(
                        demand = mergedDemand,
                        products = products
                    )

                    if (match == null) {
                        if (addOneRealPackageFallback(bucket, totalBase, displayUnit)) {
                            continue
                        }
                        unresolved += UnresolvedIngredient(
                            ingredientName = bucket.displayName,
                            quantity = totalBase,
                            unit = displayUnit,
                            reason = "Nessun prodotto sicuro trovato",
                            recipeNames = bucket.recipeNames
                        )
                        continue
                    }

                    // V37: i prodotti a peso variabile NON sono confezioni fisse.
                    // quantity_value nel catalogo Piccolo può rappresentare il peso
                    // predefinito della card (es. 500 g), mentre il prezzo reale è €/kg.
                    // In quel caso si acquista esattamente la quantità richiesta.
                    if (match.product.variableWeight) {
                        val cost = variableWeightCost(
                            product = match.product,
                            requiredBaseQuantity = totalBase,
                            family = family
                        )

                        if (cost == null) {
                            unresolved += UnresolvedIngredient(
                                ingredientName = bucket.displayName,
                                quantity = totalBase,
                                unit = displayUnit,
                                reason = "Prezzo unitario del prodotto a peso variabile non utilizzabile",
                                recipeNames = bucket.recipeNames
                            )
                            continue
                        }

                        lines += ShoppingLine(
                            ingredientName = bucket.displayName,
                            requiredQuantity = totalBase,
                            requiredUnit = displayUnit,
                            product = match.product,
                            packs = 1,
                            purchasedBaseQuantity = totalBase,
                            usedBaseQuantity = totalBase,
                            leftoverBaseQuantity = 0.0,
                            baseUnit = displayUnit,
                            totalCost = cost,
                            matchReason = match.reason + " • peso variabile",
                            recipeNames = bucket.recipeNames
                        )
                        continue
                    }

                    val pack = QuantityParser.normalizeProductQuantity(
                        product = match.product,
                        ingredientName = bucket.canonicalName
                    )

                    if (pack == null || pack.family != family) {
                        unresolved += UnresolvedIngredient(
                            ingredientName = bucket.displayName,
                            quantity = totalBase,
                            unit = displayUnit,
                            reason = "Confezione non utilizzabile in modo affidabile",
                            recipeNames = bucket.recipeNames
                        )
                        continue
                    }

                    val packs = ceil(totalBase / pack.baseValue)
                        .toInt()
                        .coerceAtLeast(1)

                    val purchased = pack.baseValue * packs
                    val cost = match.product.priceEur * packs

                    lines += ShoppingLine(
                        ingredientName = bucket.displayName,
                        requiredQuantity = totalBase,
                        requiredUnit = displayUnit,
                        product = match.product,
                        packs = packs,
                        purchasedBaseQuantity = purchased,
                        usedBaseQuantity = totalBase,
                        leftoverBaseQuantity = (purchased - totalBase)
                            .coerceAtLeast(0.0),
                        baseUnit = displayUnit,
                        totalCost = cost,
                        matchReason = match.reason,
                        recipeNames = bucket.recipeNames
                    )
                }

                continue
            }

            // Caso B: nessuna quantità convertibile.
            // Non creiamo 4 righe per 4 spicchi/cucchiai/q.b.:
            // tentiamo UNA confezione reale per l'ingrediente.
            val purchaseMatch = IngredientMatcher.bestPurchaseMatch(
                ingredientName = bucket.canonicalName,
                products = products
            )

            if (purchaseMatch != null) {
                val pack = QuantityParser.normalizeProductQuantity(
                    product = purchaseMatch.product,
                    ingredientName = bucket.canonicalName
                )

                lines += ShoppingLine(
                    ingredientName = bucket.displayName,
                    requiredQuantity = 0.0,
                    requiredUnit = "",
                    product = purchaseMatch.product,
                    packs = 1,
                    purchasedBaseQuantity = pack?.baseValue ?: 0.0,
                    usedBaseQuantity = 0.0,
                    leftoverBaseQuantity = 0.0,
                    baseUnit = pack?.displayUnit.orEmpty(),
                    totalCost = purchaseMatch.product.priceEur,
                    matchReason = purchaseMatch.reason,
                    recipeNames = bucket.recipeNames
                )
            } else {
                unresolved += UnresolvedIngredient(
                    ingredientName = bucket.displayName,
                    quantity = null,
                    unit = "",
                    reason = "Prodotto non trovato con sufficiente sicurezza",
                    recipeNames = bucket.recipeNames
                )
            }
        }

        /*
         * Ultima protezione contro duplicati semantici:
         * se due bucket finiscono sullo stesso identico prodotto reale,
         * li fondiamo in una sola riga del carrello.
         */
        val mergedLines = mergeSameProducts(lines)

        return ShoppingCalculation(
            lines = mergedLines.sortedBy {
                it.ingredientName.lowercase()
            },
            unresolved = dedupeUnresolved(unresolved).sortedBy {
                it.ingredientName.lowercase()
            },
            totalCost = mergedLines.sumOf { it.totalCost }
        )
    }

    private fun buildBuckets(
        demands: List<IngredientDemand>
    ): LinkedHashMap<String, Bucket> {
        val buckets = linkedMapOf<String, Bucket>()

        for (d in demands) {
            val canonical = canonicalShoppingIngredient(
                name = d.name,
                recipeNames = d.recipeNames
            )

            if (canonical.isBlank()) continue

            val display = displayShoppingIngredient(canonical)

            val bucket = buckets.getOrPut(canonical) {
                Bucket(
                    canonicalName = canonical,
                    displayName = display
                )
            }

            bucket.recipeNames += d.recipeNames

            val correctedDemand = normalizeRecipeDemandUnit(
                demand = d,
                canonicalIngredient = canonical
            )

            val normalized = QuantityParser.normalizeDemand(
                correctedDemand.quantity,
                correctedDemand.unit
            )

            if (normalized != null) {
                bucket.quantified += correctedDemand
            } else {
                bucket.unquantified += correctedDemand
            }
        }

        return buckets
    }

    /**
     * Correzioni di UNITA' chiaramente errate nel ricettario.
     * Non cambiamo la quantita' numerica e non inventiamo porzioni: trasformiamo
     * solo liquidi noti che erano stati codificati per errore in grammi.
     */
    private fun normalizeRecipeDemandUnit(
        demand: IngredientDemand,
        canonicalIngredient: String
    ): IngredientDemand {
        val unit = QuantityParser.canonicalUnit(demand.unit)
        return when {
            unit == "g" && canonicalIngredient.startsWith("vino ") ->
                demand.copy(unit = "ml")

            unit == "g" && (canonicalIngredient == "latte" || canonicalIngredient.startsWith("latte ")) ->
                demand.copy(unit = "ml")

            unit == "g" && canonicalIngredient.startsWith("brodo") ->
                demand.copy(unit = "ml")

            unit == "g" && (canonicalIngredient == "aceto" || canonicalIngredient.startsWith("aceto ")) ->
                demand.copy(unit = "ml")

            else -> demand
        }
    }

    /**
     * Canonicalizzazione semantica della lista spesa.
     * Qui decidiamo che nomi diversi indicano lo stesso acquisto.
     */
    private fun canonicalShoppingIngredient(
        name: String,
        recipeNames: Set<String>
    ): String {
        val n = IngredientMatcher.normalize(name)

        // Le portate "Frutta" generate senza quantità non devono comparire
        // una volta a pranzo e una a cena: nel carrello sono un'unica voce.
        val isFruitCourse = recipeNames.any {
            IngredientMatcher.normalize(it) == "frutta"
        }

        if (isFruitCourse) {
            // V50: una sola varieta' stagionale per settimana. Manteniamo
            // il nome semplice (mela, pera, uva...) così il carrello compra
            // davvero quella frutta invece di un generico prodotto "frutta".
            return n
        }

        return when {
            n == "olio" ||
                n == "olio evo" ||
                n == "olio extravergine" ||
                n == "olio extravergine di oliva" ||
                n == "olio extra vergine" ||
                n == "olio extra vergine di oliva" ->
                "olio extravergine di oliva"

            n == "aglio" ||
                n.startsWith("aglio ") ->
                "aglio"

            n == "sale" ||
                n == "sale fino" ||
                n == "sale grosso" ||
                n.startsWith("sale ") ->
                "sale"

            n == "farina" ||
                n == "farina 00" ||
                n == "farina tipo 00" ->
                "farina"

            n == "capperi" ||
                n == "capperi sotto sale" ||
                n == "capperi dissalati" ->
                "capperi"

            n == "pepe" ||
                n == "pepe nero" ->
                "pepe"

            n == "prezzemolo" ||
                n.startsWith("prezzemolo ") ->
                "prezzemolo"

            n == "basilico" ||
                n.startsWith("basilico ") ->
                "basilico"

            n in setOf(
                "cipolla", "cipolle", "cipolla bianca", "cipolle bianche",
                "cipolla ramata", "cipolle dorate", "cipolla dorata"
            ) -> "cipolla"

            n == "pomodoro" || n == "pomodori" || n == "pomodoro ramato" || n == "pomodori maturi" ->
                "pomodoro"

            n == "provola fresca o fiordilatte" || n == "provola o mozzarella" ->
                "provola o mozzarella"

            n == "pasta" || n == "pasta generica" || n == "pasta generico" ->
                "pasta corta"

            else -> n
        }
    }

    private fun displayShoppingIngredient(
        canonical: String
    ): String = when (canonical) {
        "olio extravergine di oliva" -> "Olio extravergine di oliva"
        "aglio" -> "Aglio"
        "sale" -> "Sale"
        "farina" -> "Farina"
        "capperi" -> "Capperi"
        "pepe" -> "Pepe"
        "prezzemolo" -> "Prezzemolo"
        "basilico" -> "Basilico"
        "frutta" -> "Frutta"
        else -> canonical.replaceFirstChar {
            if (it.isLowerCase()) it.titlecase() else it.toString()
        }
    }

    /**
     * Calcola il costo di una quantità realmente richiesta quando Piccolo vende
     * il prodotto a peso/volume variabile. Fallisce chiuso se unità o prezzo
     * unitario non sono coerenti con la quantità richiesta.
     */
    private fun variableWeightCost(
        product: SupermarketRepository.Product,
        requiredBaseQuantity: Double,
        family: QuantityFamily
    ): Double? {
        if (!product.variableWeight || requiredBaseQuantity <= 0.0) return null

        val unitPrice = product.unitPriceEur
            ?.takeIf { it > 0.0 }
            ?: return null

        val unitPriceFamily = QuantityParser.familyOf(product.unitPriceUnit)
        if (unitPriceFamily != family) return null

        return when (family) {
            QuantityFamily.MASS, QuantityFamily.VOLUME ->
                (requiredBaseQuantity / 1000.0) * unitPrice

            QuantityFamily.PIECE -> requiredBaseQuantity * unitPrice
            QuantityFamily.UNKNOWN -> null
        }
    }

    private fun mergeSameProducts(
        lines: List<ShoppingLine>
    ): List<ShoppingLine> {
        val map = linkedMapOf<String, ShoppingLine>()

        for (line in lines) {
            val key = line.product.key
            val existing = map[key]

            if (existing == null) {
                map[key] = line
                continue
            }

            /*
             * Se sono davvero lo stesso prodotto, sommiamo le confezioni
             * solo quando entrambe le righe avevano quantità misurate.
             * Per gli acquisti "una confezione basta" (q.b./spicchi/etc.)
             * conserviamo una sola confezione.
             */
            val bothMeasured =
                existing.requiredQuantity > 0.0 &&
                    line.requiredQuantity > 0.0 &&
                    existing.baseUnit == line.baseUnit

            if (bothMeasured) {
                val totalNeed =
                    existing.requiredQuantity +
                        line.requiredQuantity

                if (line.product.variableWeight) {
                    val family = QuantityParser.familyOf(line.requiredUnit)
                    val cost = variableWeightCost(
                        product = line.product,
                        requiredBaseQuantity = totalNeed,
                        family = family
                    )

                    if (cost != null) {
                        map[key] = existing.copy(
                            requiredQuantity = totalNeed,
                            packs = 1,
                            purchasedBaseQuantity = totalNeed,
                            usedBaseQuantity = totalNeed,
                            leftoverBaseQuantity = 0.0,
                            totalCost = cost,
                            recipeNames = existing.recipeNames + line.recipeNames
                        )
                    }
                } else {
                    val pack = QuantityParser.normalizeProductQuantity(
                        product = line.product,
                        ingredientName = line.ingredientName
                    )

                    if (pack != null && pack.baseValue > 0.0) {
                        val packs = ceil(totalNeed / pack.baseValue)
                            .toInt()
                            .coerceAtLeast(1)

                        val purchased = pack.baseValue * packs

                        map[key] = existing.copy(
                            requiredQuantity = totalNeed,
                            packs = packs,
                            purchasedBaseQuantity = purchased,
                            usedBaseQuantity = totalNeed,
                            leftoverBaseQuantity =
                                (purchased - totalNeed)
                                    .coerceAtLeast(0.0),
                            totalCost = line.product.priceEur * packs,
                            recipeNames =
                                existing.recipeNames +
                                    line.recipeNames
                        )
                    }
                }
            } else {
                map[key] = existing.copy(
                    recipeNames =
                        existing.recipeNames +
                            line.recipeNames
                )
            }
        }

        return map.values.toList()
    }

    private fun dedupeUnresolved(
        unresolved: List<UnresolvedIngredient>
    ): List<UnresolvedIngredient> {
        val map = linkedMapOf<String, UnresolvedIngredient>()

        for (item in unresolved) {
            val key = IngredientMatcher.normalize(
                item.ingredientName
            )

            val current = map[key]

            if (current == null) {
                map[key] = item
            } else {
                map[key] = current.copy(
                    recipeNames =
                        current.recipeNames +
                            item.recipeNames
                )
            }
        }

        return map.values.toList()
    }

    /**
     * V49 - usa l'eventuale margine di un budget medio/alto per passare alla
     * successiva alternativa reale compatibile dello stesso ingrediente.
     * Non cambia quantità richieste e non supera mai maxTotal.
     *
     * "Fascia superiore" qui significa soltanto alternativa reale più costosa
     * (con preferenza per un brand esplicito quando disponibile): non inventiamo
     * giudizi di qualità che il catalogo non contiene.
     */
    fun upgradeWithinBudget(
        calculation: ShoppingCalculation,
        products: List<SupermarketRepository.Product>,
        maxTotal: Double
    ): ShoppingCalculation {
        if (calculation.lines.isEmpty() || calculation.totalCost >= maxTotal - 0.01) {
            return calculation
        }

        data class Upgrade(
            val lineIndex: Int,
            val replacement: ShoppingLine,
            val delta: Double,
            val qualityGain: Int
        )

        fun lineFromMatch(
            original: ShoppingLine,
            match: IngredientMatcher.Match
        ): ShoppingLine? {
            val product = match.product

            if (original.requiredQuantity > 0.0) {
                val family = QuantityParser.familyOf(original.requiredUnit)
                if (product.variableWeight) {
                    val cost = variableWeightCost(
                        product = product,
                        requiredBaseQuantity = original.requiredQuantity,
                        family = family
                    ) ?: return null

                    return original.copy(
                        product = product,
                        packs = 1,
                        purchasedBaseQuantity = original.requiredQuantity,
                        usedBaseQuantity = original.requiredQuantity,
                        leftoverBaseQuantity = 0.0,
                        baseUnit = original.requiredUnit,
                        totalCost = cost,
                        matchReason = match.reason + " • fascia superiore"
                    )
                }

                val pack = QuantityParser.normalizeProductQuantity(
                    product = product,
                    ingredientName = original.ingredientName
                ) ?: return null
                if (pack.family != family || pack.baseValue <= 0.0) return null

                val packs = ceil(original.requiredQuantity / pack.baseValue)
                    .toInt()
                    .coerceAtLeast(1)
                val purchased = pack.baseValue * packs

                return original.copy(
                    product = product,
                    packs = packs,
                    purchasedBaseQuantity = purchased,
                    usedBaseQuantity = original.requiredQuantity,
                    leftoverBaseQuantity = (purchased - original.requiredQuantity).coerceAtLeast(0.0),
                    baseUnit = pack.displayUnit,
                    totalCost = product.priceEur * packs,
                    matchReason = match.reason + " • fascia superiore"
                )
            }

            val pack = QuantityParser.normalizeProductQuantity(
                product = product,
                ingredientName = original.ingredientName
            )
            return original.copy(
                product = product,
                packs = 1,
                purchasedBaseQuantity = pack?.baseValue ?: 0.0,
                usedBaseQuantity = 0.0,
                leftoverBaseQuantity = 0.0,
                baseUnit = pack?.displayUnit.orEmpty(),
                totalCost = product.priceEur,
                matchReason = match.reason + " • fascia superiore"
            )
        }

        fun alternatives(original: ShoppingLine): List<ShoppingLine> {
            val matches = if (original.requiredQuantity > 0.0) {
                IngredientMatcher.purchaseOptions(
                    demand = IngredientDemand(
                        name = original.ingredientName,
                        quantity = original.requiredQuantity,
                        unit = original.requiredUnit,
                        recipeNames = original.recipeNames
                    ),
                    products = products,
                    maxOptions = 10
                )
            } else {
                IngredientMatcher.purchasePackageOptions(
                    ingredientName = original.ingredientName,
                    products = products,
                    maxOptions = 10
                )
            }

            return matches
                .asSequence()
                .filter { it.product.key != original.product.key }
                .mapNotNull { lineFromMatch(original, it) }
                .filter { it.totalCost > original.totalCost + 0.02 }
                .distinctBy { it.product.key }
                .toList()
        }

        val lines = calculation.lines.toMutableList()
        var total = calculation.totalCost
        var steps = 0

        while (steps < 20) {
            steps++
            val headroom = maxTotal - total
            if (headroom <= 0.02) break

            val upgrades = mutableListOf<Upgrade>()
            lines.forEachIndexed { index, line ->
                alternatives(line).forEach { replacement ->
                    val delta = replacement.totalCost - line.totalCost
                    if (delta > 0.02 && delta <= headroom + 0.0001) {
                        fun qualitySignals(product: SupermarketRepository.Product): Int {
                            val text = IngredientMatcher.normalize(
                                product.name + " " + (product.brand ?: "")
                            )
                            val strong = listOf(
                                "dop", "igp", "biologico", "bio ", "alta qualita",
                                "100 italiano", "100% italiano", "filiera", "fresco"
                            ).count { text.contains(it) }
                            val brand = if (!product.brand.isNullOrBlank()) 1 else 0
                            return strong * 3 + brand
                        }

                        val qualityGain =
                            qualitySignals(replacement.product) - qualitySignals(line.product)
                        upgrades += Upgrade(index, replacement, delta, qualityGain)
                    }
                }
            }

            val chosen = upgrades.maxWithOrNull(
                compareBy<Upgrade> { it.qualityGain }
                    .thenBy { it.delta }
            ) ?: break

            lines[chosen.lineIndex] = chosen.replacement
            total += chosen.delta
        }

        return ShoppingCalculation(
            lines = lines.sortedBy { it.ingredientName.lowercase() },
            unresolved = calculation.unresolved,
            totalCost = lines.sumOf { it.totalCost }
        )
    }

}