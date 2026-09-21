package com.example.smartcampania

/*
 * V58 — aggiunto match stretto per guanciale (Carbonara).
 */


// V57 — nuovi alimenti per ricette economiche napoletane/casertane.

import java.text.Normalizer
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.abs
import kotlin.math.ceil

/**
 * V33 - Matching "umano" e fail-closed.
 *
 * Principio:
 * - la ricetta è la sorgente della verità;
 * - un prodotto può entrare nel carrello SOLO se il suo nome contiene
 *   il nome dell'ingrediente oppure un sinonimo esplicitamente ammesso;
 * - niente più punteggi fuzzy basati su parole generiche;
 * - fra i prodotti semanticamente corretti scegliamo quello che copre
 *   il fabbisogno con il costo reale più basso;
 * - scartiamo confezioni professionali/sproporzionate.
 */
internal object IngredientMatcher {

    data class Match(
        val product: SupermarketRepository.Product,
        val score: Int,
        val reason: String
    )

    private data class PreparedProduct(
        val name: String,
        val paddedName: String
    )

    private val preparedCache = ConcurrentHashMap<String, PreparedProduct>()
    private val candidateCache = ConcurrentHashMap<String, List<SupermarketRepository.Product>>()

    // V64 FAST — la normalizzazione è una funzione pura e viene richiamata
    // migliaia di volte durante la generazione. Memorizzare il risultato delle
    // stesse stringhe elimina regex/Normalizer ripetuti senza cambiare il matching.
    private val normalizeCache = ConcurrentHashMap<String, String>()

    private data class ProductTokenIndex(
        val byToken: Map<String, List<SupermarketRepository.Product>>
    )

    private val productIndexCache = ConcurrentHashMap<String, ProductTokenIndex>()

    // V44: cache anche del risultato finale del matching. Durante la creazione
    // del menu gli stessi ingredienti/quantità ricorrono molte volte: non ha
    // senso riesaminare migliaia di prodotti ogni volta.
    private val measuredMatchCache = ConcurrentHashMap<String, Match>()
    private val purchaseMatchCache = ConcurrentHashMap<String, Match>()
    private val negativeMatchCache = ConcurrentHashMap.newKeySet<String>()

    /*
     * Solo equivalenze che una persona considererebbe lo stesso acquisto.
     * Niente associazioni "creative".
     */
    private val approvedAliases: Map<String, Set<String>> = mapOf(
        "olio extravergine di oliva" to setOf(
            "olio extravergine di oliva",
            "olio extra vergine di oliva",
            "olio extravergine",
            "olio evo"
        ),
        "aglio" to setOf("aglio"),
        "sale" to setOf("sale"),
        "pepe" to setOf("pepe nero", "pepe"),
        "pepe nero" to setOf("pepe nero"),
        "basilico" to setOf("basilico"),
        "prezzemolo" to setOf("prezzemolo"),
        "peperoncino" to setOf("peperoncino", "peperoncino rosso", "peperoncini"),
        "peperoncino essiccato" to setOf("peperoncino essiccato", "peperoncino macinato"),
        "origano" to setOf("origano"),
        "rosmarino" to setOf("rosmarino"),
        "menta" to setOf("menta"),
        "capperi" to setOf("capperi", "cappero"),
        "capperi sotto sale" to setOf("capperi sotto sale", "capperi"),
        "acciughe" to setOf("acciughe", "acciuga", "alici", "alice", "filetti di acciuga"),
        "acciughe sott olio" to setOf("acciughe sott olio", "filetti di acciuga sott olio", "filetti di alici sott olio"),
        "filetti di acciuga" to setOf("filetti di acciuga", "filetti di alici", "acciughe"),
        "alici sotto sale" to setOf("alici sotto sale", "acciughe sotto sale"),
        "alici fresche" to setOf("alici fresche", "alici"),
        "olio di semi di arachidi" to setOf("olio di semi di arachidi", "olio di semi di arachide", "olio arachidi"),
        "olio di semi per frittura" to setOf("olio di semi", "olio di semi di girasole", "olio di girasole", "olio per friggere"),
        "olio per friggere" to setOf("olio di semi", "olio di semi di girasole", "olio di girasole", "olio per friggere"),

        "pasta" to setOf("pasta"),
        "pasta mista" to setOf("pasta mista"),
        "pasta corta" to setOf("pasta corta", "penne", "rigatoni", "fusilli", "mezze maniche"),
        "spaghetti" to setOf("spaghetti"),
        "linguine" to setOf("linguine"),
        "linguine fresche" to setOf("linguine fresche", "linguine"),
        "paccheri" to setOf("paccheri"),
        "mezzi paccheri" to setOf("mezzi paccheri"),
        "scialatielli" to setOf("scialatielli"),
        "scialatielli freschi" to setOf("scialatielli freschi", "scialatielli"),
        "ziti" to setOf("ziti"),
        "lasagne" to setOf("lasagne"),
        "riso" to setOf("riso"),

        "pomodoro" to setOf("pomodoro"),
        "pomodori" to setOf("pomodori", "pomodoro"),
        "pomodorini" to setOf("pomodorini", "pomodoro ciliegino", "ciliegino", "pomodoro ciliegia", "pomodoro datterino", "datterino"),
        "pomodori pelati" to setOf("pomodori pelati", "pelati"),
        "pomodori san marzano pelati" to setOf("san marzano pelati", "pomodori pelati"),
        "pomodori pelati san marzano" to setOf("san marzano pelati", "pomodori pelati"),
        "passata di pomodoro" to setOf("passata di pomodoro", "passata"),
        "salsa di pomodoro" to setOf("salsa di pomodoro", "passata di pomodoro"),
        "pomodori del piennolo" to setOf("pomodori del piennolo", "pomodorini del piennolo"),
        "pomodorini del piennolo" to setOf("pomodorini del piennolo", "pomodori del piennolo"),

        "zucchine" to setOf("zucchine", "zucchina"),
        "melanzane" to setOf("melanzane", "melanzana"),
        "patate" to setOf("patate", "patata"),
        "patate a pasta gialla" to setOf("patate", "patata"),
        "peperoni" to setOf("peperoni", "peperone"),
        "peperone rosso" to setOf("peperone rosso", "peperoni rossi"),
        // V57: ampliamento contorni/primi economici. Alias stretti, niente match generici.
        "cavolfiore" to setOf("cavolfiore", "cavolfiori"),
        "fagiolini" to setOf("fagiolini", "fagiolino"),
        "verza" to setOf("verza", "cavolo verza"),
        "lattuga" to setOf("lattuga", "lattuga romana", "lattuga incappucciata"),
        "friggitelli" to setOf("friggitelli", "frigitelli", "peperoncini verdi dolci", "peperoncini verdi"),
        "scarola" to setOf("scarola"),
        "friarielli" to setOf("friarielli"),
        "broccoli" to setOf("broccoli", "broccolo"),
        "cicoria" to setOf("cicoria"),
        "finocchi" to setOf("finocchi", "finocchio"),
        "finocchio" to setOf("finocchio", "finocchi"),
        "carciofi" to setOf("carciofi", "carciofo"),
        "zucca" to setOf("zucca"),
        "cipolla" to setOf("cipolla", "cipolle"),
        "cipolla bianca" to setOf("cipolla bianca", "cipolle bianche"),
        "cipolla ramata" to setOf("cipolla ramata", "cipolla"),
        "cipolle dorate" to setOf("cipolle dorate", "cipolla dorata"),
        "carota" to setOf("carota", "carote"),
        "sedano" to setOf("sedano"),

        // V50: la frutta viene mostrata con il nome semplice, ma il matcher
        // può agganciare le cultivar reali Piccolo che contengono quel nome.
        "mela" to setOf("mela", "mele"),
        "pera" to setOf("pera", "pere"),
        "pesca" to setOf("pesca", "pesche"),
        "percoca" to setOf("percoca", "percoche"),
        "uva" to setOf("uva"),
        "sfogliatella riccia" to setOf("sfogliatella riccia"),
        "sfogliatella frolla" to setOf("sfogliatella frolla"),
        "baba pronto" to setOf("baba fatto da noi", "baba"),
        "pastiera pronta" to setOf("pastiera ns produzione", "pastiera"),
        "biscotto amarena pronto" to setOf("biscotto amarena"),
        "delizia limone pronta" to setOf("delizia al limone"),
        "albicocca" to setOf("albicocca", "albicocche"),
        "susina" to setOf("susina", "susine", "prugna", "prugne"),
        "melone" to setOf("melone", "meloni"),
        "anguria" to setOf("anguria", "cocomero"),
        "fico" to setOf("fico", "fichi"),
        "kiwi" to setOf("kiwi"),

        "fagioli cotti" to setOf("fagioli", "fagioli cotti"),
        "fagioli lessi" to setOf("fagioli", "fagioli lessi"),
        "fagioli cannellini cotti" to setOf("fagioli cannellini", "cannellini"),
        "ceci cotti" to setOf("ceci", "ceci cotti"),
        "piselli" to setOf("piselli", "pisello"),
        "lenticchie" to setOf("lenticchie", "lenticchia"),

        "mozzarella" to setOf("mozzarella"),
        "mozzarella di bufala" to setOf("mozzarella di bufala", "bufala"),
        "provola" to setOf("provola"),
        "provola affumicata" to setOf("provola affumicata"),
        "provola fresca o fiordilatte" to setOf("provola", "fiordilatte", "fior di latte"),
        "provola o mozzarella" to setOf("provola", "fiordilatte", "fior di latte", "mozzarella"),
        "provolone" to setOf("provolone"),
        "provolone del monaco o provolone" to setOf("provolone del monaco", "provolone"),
        "caciocavallo" to setOf("caciocavallo"),
        "parmigiano" to setOf("parmigiano"),
        "parmigiano reggiano" to setOf("parmigiano reggiano", "parmigiano"),
        "parmigiano grattugiato" to setOf("parmigiano grattugiato", "parmigiano"),
        "pecorino" to setOf("pecorino"),
        "pecorino romano" to setOf("pecorino romano", "pecorino"),
        "pecorino o parmigiano" to setOf("pecorino", "parmigiano"),
        "parmigiano o caciocavallo" to setOf("parmigiano", "caciocavallo"),
        "formaggio grattugiato" to setOf(
            "formaggio grattugiato",
            "parmigiano grattugiato",
            "parmigiano reggiano grattugiato",
            "grana grattugiato",
            "grana padano grattugiato"
        ),
        "grana" to setOf("grana"),
        "latte" to setOf("latte"),
        "ricotta" to setOf("ricotta"),
        "latte intero" to setOf("latte intero", "latte fresco intero"),
        "burro" to setOf("burro"),
        "burro per besciamella" to setOf("burro"),

        "uova" to setOf("uova", "uovo"),
        "albumi" to setOf("albume", "albumi"),
        "tuorli d uovo" to setOf("tuorlo", "tuorli"),

        "salsiccia" to setOf("salsiccia", "salsicce"),
        "salsicce" to setOf("salsiccia", "salsicce"),
        "carne macinata" to setOf("carne macinata", "macinato"),
        "carne trita di manzo" to setOf("macinato di manzo", "carne macinata di manzo"),
        // Piccolo espone i tagli bovini da cottura con denominazioni di banco.
        "polpa di manzo" to setOf(
            "polpa di manzo",
            "bovino palettina",
            "bovino arrosto disossato",
            "bollito di bovino"
        ),
        "fettina di manzo" to setOf("fettina di manzo", "fettine di manzo"),
        "fettine di scamone di manzo" to setOf("scamone di manzo", "fettine di scamone"),
        "fettine di vitello" to setOf("fettine di vitello", "vitello"),
        "bistecca di vitello" to setOf("bistecca di vitello", "vitello bistecca"),
        "bistecca di bovino" to setOf("bistecca di bovino", "bovino bistecca"),
        "filetto di bovino" to setOf("filetto di bovino", "bovino filetto"),
        "fettine di lonza" to setOf("fettine di lonza", "lonza"),
        "petto di pollo" to setOf("petto di pollo", "petto pollo", "pollo petto"),
        "petto di pollo a fette" to setOf("petto di pollo", "petto pollo"),
        "lonza di maiale a fette" to setOf("lonza di maiale", "lonza"),
        "costine di maiale" to setOf(
            "costine di maiale",
            "costine di suino",
            "costine",
            "costolette di maiale",
            "tracchie di maiale"
        ),
        "costoletta di maiale" to setOf("costoletta di maiale", "costoletta"),
        "trippa" to setOf("trippa", "trippa bovina", "trippa di bovino"),
        "agnello" to setOf("agnello"),
        "coniglio" to setOf("coniglio"),
        "guanciale" to setOf("guanciale"),
        "prosciutto cotto" to setOf("prosciutto cotto"),
        "prosciutto crudo" to setOf("prosciutto crudo"),
        "salame napoli" to setOf("salame napoli", "salame napoletano"),
        "salame napoletano" to setOf("salame napoletano", "salame napoli"),
        "capocollo" to setOf("capocollo"),

        "cozze" to setOf("cozze"),
        "vongole" to setOf("vongole"),
        "vongole veraci" to setOf("vongole veraci", "vongole"),
        "polpo" to setOf("polpo"),
        "polpo verace" to setOf("polpo verace", "polpo"),
        "calamari" to setOf("calamari", "calamaro"),
        "calamaretti" to setOf("calamaretti", "calamaro"),
        "gamberi" to setOf("gamberi", "gambero", "gamberoni", "gamberone"),
        "gamberi o calamaretti" to setOf("gamberi", "calamaretti"),
        "scampi" to setOf("scampi", "scampo"),
        "seppie pulite" to setOf("seppie", "seppia"),
        "seppioline" to setOf("seppioline", "seppie"),
        "totani" to setOf("totani", "totano"),
        "anelli di totano" to setOf("anelli di totano", "anelli totano"),
        "moscardini" to setOf("moscardini", "moscardino"),
        "pesce spada" to setOf("pesce spada", "spada iwp"),
        "filetto di pesce spada" to setOf("pesce spada", "spada iwp"),
        "tonno fresco" to setOf("tonno fresco", "filetto di tonno"),
        "filetto di tonno" to setOf("filetto di tonno", "tonno fresco"),
        "orata" to setOf("orata"),
        "filetti di orata" to setOf("filetti di orata", "orata"),
        "branzino" to setOf("branzino"),
        "filetto di branzino" to setOf("filetto di branzino", "filetti di branzino", "branzino"),
        "tonno sott olio sgocciolato" to setOf("tonno sott olio"),
        "tonno al naturale" to setOf("tonno al naturale"),
        "sgombro al naturale" to setOf("sgombro al naturale"),
        "sgombro pulito" to setOf("sgombro"),
        "tranci di spigola" to setOf("spigola", "branzino"),
        "baccala ammollato" to setOf("baccala"),
        "frutti di mare misti" to setOf("frutti di mare"),
        "sparnocchie" to setOf("sparnocchie", "canocchie"),

        "farina" to setOf("farina"),
        "farina 00" to setOf("farina 00", "farina"),
        "pane" to setOf("pane"),
        "pane raffermo" to setOf("pane"),
        "mollica di pane" to setOf("pane", "mollica"),
        "pangrattato" to setOf("pangrattato", "pane grattugiato"),
        "pancarre" to setOf("pancarre", "pane per tramezzini"),
        "impasto pizza" to setOf("impasto pizza"),

        "olive" to setOf("olive"),
        "olive nere" to setOf("olive nere"),
        "olive verdi" to setOf("olive verdi"),
        "olive di gaeta" to setOf("olive di gaeta"),
        "olive nere di gaeta" to setOf("olive nere di gaeta", "olive di gaeta"),
        "pinoli" to setOf("pinoli"),
        "uva passa" to setOf("uva passa", "uvetta"),
        "uvetta" to setOf("uvetta", "uva passa"),
        "mandorle spellate" to setOf("mandorle"),
        "zucchero bianco" to setOf("zucchero"),
        "zucchero a velo" to setOf("zucchero a velo"),
        "cioccolato fondente" to setOf("cioccolato fondente"),
        "lievito" to setOf("lievito"),
        "noce moscata" to setOf("noce moscata"),

        "aceto" to setOf("aceto"),
        "aceto di vino bianco" to setOf("aceto di vino bianco", "aceto bianco"),
        "vino bianco" to setOf("vino bianco"),
        "vino bianco secco" to setOf("vino bianco secco", "vino bianco"),
        "vino bianco falanghina" to setOf("falanghina"),
        "vino rosso" to setOf("vino rosso"),
        "brandy" to setOf("brandy"),
        "brodo vegetale" to setOf("brodo vegetale"),
        "limone" to setOf("limone", "limoni"),
        "limone amalfitano" to setOf("limone", "limoni")
    )

    /**
     * Match per quantità misurabili.
     */
    fun bestMatch(
        demand: IngredientDemand,
        products: List<SupermarketRepository.Product>
    ): Match? {
        val need = QuantityParser.normalizeDemand(demand.quantity, demand.unit)
            ?: return null

        val ingredient = canonicalInput(demand.name)
        val phrases = allowedPhrases(ingredient)
        if (phrases.isEmpty()) return null

        val cacheKey = buildString {
            append(System.identityHashCode(products))
            append('|').append(products.size)
            append('|').append(ingredient)
            append('|').append(need.family.name)
            append('|').append(kotlin.math.round(need.baseValue * 100.0) / 100.0)
        }
        measuredMatchCache[cacheKey]?.let { return it }
        if (negativeMatchCache.contains(cacheKey)) return null

        var best: Match? = null
        var bestCost = Double.POSITIVE_INFINITY
        var bestWaste = Double.POSITIVE_INFINITY

        for (product in resolvedCandidates(ingredient, phrases, products)) {
            val pack = QuantityParser.normalizeProductQuantity(
                product = product,
                ingredientName = ingredient
            ) ?: continue

            if (pack.family != need.family || pack.baseValue <= 0.0) continue
            val isConadReferenceVariable =
                normalize(product.supermarket).contains("conad") &&
                    product.variableWeight &&
                    product.key.startsWith("REF:")
            val isConadVerifiedMaldon =
                ingredient == "sale" &&
                    normalize(product.supermarket).contains("conad") &&
                    normalize(product.name).contains("maldon")
            if (!isConadReferenceVariable && !isConadVerifiedMaldon &&
                !reasonablePack(ingredient, need.baseValue, pack)) continue
            if (!realisticSpecialPack(ingredient, pack)) continue
            if (!isConadVerifiedMaldon &&
                !realisticProductForIngredient(ingredient, product, pack)) continue

            val packs = ceil(need.baseValue / pack.baseValue)
                .toInt()
                .coerceAtLeast(1)

            // V43: il criterio principale e' il COSTO REALE che serve per coprire
            // il fabbisogno, non la dimensione "ideale" della confezione.
            // Per i prodotti a peso variabile usiamo direttamente il prezzo/kg
            // o prezzo/litro, cosi' 700 g di verdura vengono confrontati sul costo
            // di 700 g e non sul prezzo della pezzatura indicativa della card.
            val cost = if (product.variableWeight && product.unitPriceEur != null) {
                val unit = normalize(product.unitPriceUnit.orEmpty())
                when {
                    need.family == QuantityFamily.MASS && unit == "kg" ->
                        (need.baseValue / 1000.0) * product.unitPriceEur
                    need.family == QuantityFamily.VOLUME && unit in setOf("l", "lt", "litro") ->
                        (need.baseValue / 1000.0) * product.unitPriceEur
                    need.family == QuantityFamily.PIECE && unit in setOf("pz", "pezzo") ->
                        need.baseValue * product.unitPriceEur
                    else -> product.priceEur * packs
                }
            } else {
                product.priceEur * packs
            }
            val waste = if (product.variableWeight) 0.0 else pack.baseValue * packs - need.baseValue
            val packPenalty = householdPackPenalty(ingredient, pack, need.baseValue)

            val candidate = Match(
                product = product,
                score = 100,
                reason = "corrispondenza ingrediente verificata"
            )

            val currentPenalty = best?.product?.let { bp ->
                QuantityParser.normalizeProductQuantity(bp, ingredient)
                    ?.let { householdPackPenalty(ingredient, it, need.baseValue) }
                    ?: Double.POSITIVE_INFINITY
            } ?: Double.POSITIVE_INFINITY

            // V43: fra prodotti semanticamente corretti e confezioni realistiche
            // scegliamo PRIMA il costo totale piu' basso. Pezzatura e spreco
            // servono solo come spareggio.
            val better =
                cost < bestCost - 0.005 ||
                    (abs(cost - bestCost) <= 0.005 &&
                        (packPenalty < currentPenalty - 0.15 ||
                            (abs(packPenalty - currentPenalty) <= 0.15 && waste < bestWaste)))

            if (best == null || better) {
                best = candidate
                bestCost = cost
                bestWaste = waste
            }
        }

        if (best != null) {
            measuredMatchCache[cacheKey] = best
        } else {
            negativeMatchCache += cacheKey
        }
        return best
    }

    /**
     * V49 - alternative reali ordinate per costo per uno stesso fabbisogno.
     * Serve soltanto alla fase finale: con un budget più alto l'app può salire
     * di fascia senza cambiare ingrediente, quantità o sicurezza del matching.
     */
    fun purchaseOptions(
        demand: IngredientDemand,
        products: List<SupermarketRepository.Product>,
        maxOptions: Int = 12
    ): List<Match> {
        val ingredient = canonicalInput(demand.name)
        val phrases = allowedPhrases(ingredient)
        if (phrases.isEmpty()) return emptyList()

        val need = QuantityParser.normalizeDemand(demand.quantity, demand.unit)
            ?: return emptyList()

        data class Ranked(val match: Match, val cost: Double, val waste: Double)
        val ranked = mutableListOf<Ranked>()

        for (product in resolvedCandidates(ingredient, phrases, products)) {
            val pack = QuantityParser.normalizeProductQuantity(
                product = product,
                ingredientName = ingredient
            ) ?: continue
            if (pack.family != need.family || pack.baseValue <= 0.0) continue
            val isConadReferenceVariable =
                normalize(product.supermarket).contains("conad") &&
                    product.variableWeight &&
                    product.key.startsWith("REF:")
            val isConadVerifiedMaldon =
                ingredient == "sale" &&
                    normalize(product.supermarket).contains("conad") &&
                    normalize(product.name).contains("maldon")
            if (!isConadReferenceVariable && !isConadVerifiedMaldon &&
                !reasonablePack(ingredient, need.baseValue, pack)) continue
            if (!realisticSpecialPack(ingredient, pack)) continue
            if (!isConadVerifiedMaldon &&
                !realisticProductForIngredient(ingredient, product, pack)) continue

            val packs = ceil(need.baseValue / pack.baseValue).toInt().coerceAtLeast(1)
            val cost = if (product.variableWeight && product.unitPriceEur != null) {
                val unit = normalize(product.unitPriceUnit.orEmpty())
                when {
                    need.family == QuantityFamily.MASS && unit == "kg" ->
                        (need.baseValue / 1000.0) * product.unitPriceEur
                    need.family == QuantityFamily.VOLUME && unit in setOf("l", "lt", "litro") ->
                        (need.baseValue / 1000.0) * product.unitPriceEur
                    need.family == QuantityFamily.PIECE && unit in setOf("pz", "pezzo") ->
                        need.baseValue * product.unitPriceEur
                    else -> product.priceEur * packs
                }
            } else {
                product.priceEur * packs
            }
            val waste = if (product.variableWeight) 0.0 else pack.baseValue * packs - need.baseValue
            ranked += Ranked(
                Match(product, 100, "corrispondenza ingrediente verificata"),
                cost,
                waste
            )
        }

        return ranked
            .sortedWith(compareBy<Ranked> { it.cost }.thenBy { it.waste })
            .map { it.match }
            .distinctBy { it.product.key }
            .take(maxOptions.coerceAtLeast(1))
    }

    fun purchasePackageOptions(
        ingredientName: String,
        products: List<SupermarketRepository.Product>,
        maxOptions: Int = 12
    ): List<Match> {
        val ingredient = canonicalInput(ingredientName)
        val phrases = allowedPhrases(ingredient)
        if (phrases.isEmpty()) return emptyList()

        return resolvedCandidates(ingredient, phrases, products)
            .mapNotNull { product ->
                val pack = QuantityParser.normalizeProductQuantity(product, ingredient)
                if (pack != null && !reasonableUnknownNeedPack(ingredient, pack)) return@mapNotNull null
                if (pack != null && !realisticSpecialPack(ingredient, pack)) return@mapNotNull null
                if (pack != null && !realisticProductForIngredient(ingredient, product, pack)) return@mapNotNull null
                Match(product, 100, "corrispondenza ingrediente verificata")
            }
            .sortedBy { it.product.priceEur }
            .distinctBy { it.product.key }
            .take(maxOptions.coerceAtLeast(1))
    }

    /**
     * Match per q.b., spicchi, cucchiai ecc.
     * Qui non inventiamo una quantità: scegliamo UNA confezione domestica,
     * ma sempre e soltanto se il nome del prodotto è semanticamente corretto.
     */
    fun bestPurchaseMatch(
        ingredientName: String,
        products: List<SupermarketRepository.Product>
    ): Match? {
        val ingredient = canonicalInput(ingredientName)
        val phrases = allowedPhrases(ingredient)
        if (phrases.isEmpty()) return null

        val cacheKey = buildString {
            append("purchase|")
            append(System.identityHashCode(products))
            append('|').append(products.size)
            append('|').append(ingredient)
        }
        purchaseMatchCache[cacheKey]?.let { return it }
        if (negativeMatchCache.contains(cacheKey)) return null

        var best: Match? = null
        var bestPrice = Double.POSITIVE_INFINITY
        var bestPack = Double.POSITIVE_INFINITY

        for (product in resolvedCandidates(ingredient, phrases, products)) {
            val pack = QuantityParser.normalizeProductQuantity(
                product = product,
                ingredientName = ingredient
            )

            val isConadVerifiedMaldon =
                ingredient == "sale" &&
                    normalize(product.supermarket).contains("conad") &&
                    normalize(product.name).contains("sale marino maldon")
            if (pack != null && !reasonableUnknownNeedPack(ingredient, pack)) {
                continue
            }
            if (pack != null && !realisticSpecialPack(ingredient, pack)) continue
            if (pack != null && !isConadVerifiedMaldon &&
                !realisticProductForIngredient(ingredient, product, pack)) continue

            val packSize = pack?.baseValue ?: Double.POSITIVE_INFINITY
            val target = when {
                ingredient.contains("olio") -> 1000.0
                ingredient == "sale" -> 1000.0
                ingredient.contains("mozzarella") || ingredient.contains("provola") || ingredient.contains("fiordilatte") -> 250.0
                ingredient.contains("vino") -> 750.0
                ingredient == "uova" -> 6.0
                else -> packSize
            }
            val packPenalty = if (pack != null && target.isFinite() && target > 0.0) {
                abs(pack.baseValue - target) / target
            } else 0.0
            val currentPenalty = if (best != null) {
                val bp = QuantityParser.normalizeProductQuantity(best.product, ingredient)
                if (bp != null && target.isFinite() && target > 0.0) abs(bp.baseValue - target) / target else Double.POSITIVE_INFINITY
            } else Double.POSITIVE_INFINITY

            // V69: Famila espone gli sfusi con priceEur espresso per grammo.
            // Per q.b./pezzi non convertibili confrontiamo quindi il costo
            // della pezzatura di acquisto, non il minuscolo prezzo di 1 grammo.
            // Piccolo e Decò restano invariati perché per loro priceEur è già
            // il prezzo della confezione/card reale.
            val comparablePrice =
                if (product.variableWeight && product.unitPriceEur != null && pack != null) {
                    when (pack.family) {
                        QuantityFamily.MASS, QuantityFamily.VOLUME ->
                            (pack.baseValue / 1000.0) * product.unitPriceEur
                        QuantityFamily.PIECE -> pack.baseValue * product.unitPriceEur
                        QuantityFamily.UNKNOWN -> product.priceEur
                    }
                } else {
                    product.priceEur
                }

            val better =
                comparablePrice < bestPrice - 0.005 ||
                    (abs(comparablePrice - bestPrice) <= 0.005 &&
                        (packPenalty < currentPenalty - 0.15 ||
                            (abs(packPenalty - currentPenalty) <= 0.15 && packSize < bestPack)))

            if (best == null || better) {
                best = Match(
                    product = product,
                    score = 100,
                    reason = "corrispondenza ingrediente verificata"
                )
                bestPrice = comparablePrice
                bestPack = packSize
            }
        }

        if (best != null) {
            purchaseMatchCache[cacheKey] = best
        } else {
            negativeMatchCache += cacheKey
        }
        return best
    }

    private fun canonicalInput(value: String): String {
        val n = normalize(value)
        return when (n) {
            "olio", "olio evo", "olio extra vergine",
            "olio extra vergine di oliva", "olio extravergine" ->
                "olio extravergine di oliva"
            "sale fino", "sale grosso" -> "sale"
            "pepe" -> "pepe"
            "prezzemolo fresco" -> "prezzemolo"
            "menta fresca" -> "menta"
            "zucchine medie" -> "zucchine"
            "peperoni di colori diversi" -> "peperoni"
            "pomodori maturi", "pomodoro ramato" -> "pomodoro"
            "pomodorini pizzutelli" -> "pomodorini"
            "pasta", "pasta generica", "pasta generico" -> "pasta corta"
            else -> n
        }
    }

    private fun allowedPhrases(ingredient: String): Set<String> {
        approvedAliases[ingredient]?.let { return it.map(::normalize).toSet() }

        /*
         * Se non abbiamo un alias esplicito, usiamo SOLO il nome completo.
         * Questo è intenzionalmente conservativo.
         */
        return if (ingredient.length >= 3) setOf(ingredient) else emptySet()
    }

    private fun strictSemanticMatch(
        ingredient: String,
        phrases: Set<String>,
        productName: String,
        paddedProductName: String
    ): Boolean {
        val hit = when (ingredient) {
            "latte intero" ->
                containsPhrase(paddedProductName, "latte") &&
                    containsPhrase(paddedProductName, "intero")

            "formaggio grattugiato" ->
                productName.contains("grattugiat") &&
                    (
                        containsPhrase(paddedProductName, "parmigiano") ||
                        containsPhrase(paddedProductName, "grana") ||
                        containsPhrase(paddedProductName, "formaggio")
                    )

            else ->
                phrases.any { phrase ->
                    phrase.isNotBlank() && containsPhrase(paddedProductName, phrase)
                }
        }
        if (!hit) return false

        // Esclusioni che evitano omonimie/falsi positivi.
        if (ingredient == "sale" && containsPhrase(paddedProductName, "salame")) return false
        if (ingredient == "sale" && (
                productName.contains("primo sale") ||
                productName.contains("senza sale") ||
                productName.contains("senza sale aggiunto")
            )) return false
        if (ingredient == "sale" &&
            !productName.contains("sale marino maldon") &&
            listOf(
                "himalaya", "rosa", "maldon", "fiocchi", "affumicato",
                "aromatizzato", "gourmet", "nero di", "blu di"
            ).any { productName.contains(it) }) return false
        if (ingredient == "pepe" && containsPhrase(paddedProductName, "peperoni")) return false

        if (ingredient in setOf("acciughe", "filetti di acciuga") &&
            (productName.contains("pasta di acciug") ||
             productName.contains("pasta d acciug") ||
             productName.contains("salsa") ||
             productName.contains("condimento"))
        ) return false

        if (ingredient.contains("extravergine") &&
            !productName.contains("extravergine") &&
            !productName.contains("extra vergine") &&
            !productName.contains("evo")
        ) return false

        if (ingredient.contains("sott olio") && !productName.contains("olio")) return false
        if (ingredient.contains("fresco") &&
            (productName.contains("sott olio") ||
             productName.contains("sotto sale") ||
             productName.contains("conserva"))
        ) return false

        if (ingredient.contains("crudo") && !productName.contains("crudo")) return false
        if (ingredient.contains("cotto") && !productName.contains("cotto")) return false

        // Tagli di carne: la specie da sola non basta. "Fettine di vitello"
        // non può diventare hamburger di vitello e un petto di pollo non può
        // diventare un generico prodotto di pollo.
        if (ingredient == "fettine di vitello" &&
            !(productName.contains("vitello") && productName.contains("fettin"))) return false
        if (ingredient == "fettine di lonza" || ingredient == "lonza di maiale a fette") {
            if (!productName.contains("lonza") || !productName.contains("fettin")) return false
        }
        if (ingredient == "petto di pollo" || ingredient == "petto di pollo a fette") {
            if (!productName.contains("pollo") || !productName.contains("petto")) return false
        }

        if (ingredient == "latte intero") {
            if (!containsPhrase(paddedProductName, "latte") || !containsPhrase(paddedProductName, "intero")) return false
            if (productName.contains("parzialmente scremato") || productName.contains("scremato")) return false
        }

        if (ingredient == "formaggio grattugiato" && !productName.contains("grattugiat")) return false

        // Pesce: non sostituire la materia prima con piatti pronti/misti.
        if (ingredient.startsWith("polpo") &&
            listOf("carpaccio", "cotto", "insalata", "piatto pronto").any { productName.contains(it) }
        ) return false

        if (ingredient.startsWith("vongol") &&
            listOf("misto mare", "tentazioni", "sugo", "condimento", "piatto pronto").any { productName.contains(it) }
        ) return false

        if (ingredient.contains("costine") &&
            listOf("slow cooked", "barbecue", "bbq", "precotto").any { productName.contains(it) }
        ) return false

        // V61 SAFETY: omonimie reali trovate durante il collaudo sul DB Piccolo.
        if (ingredient == "aglio" &&
            (productName.contains("senza aglio") || productName.contains("senz aglio"))
        ) return false

        if (ingredient == "prezzemolo" && listOf(
                "gratinat", "spiedin", "filetto", "merluzzo", "piatto pronto", "sugo", "salsa"
            ).any { productName.contains(it) }) return false

        if (ingredient == "basilico" && listOf(
                "pesto", "sugo", "salsa", "piatto pronto"
            ).any { productName.contains(it) }) return false

        if (ingredient == "rosmarino" && listOf(
                "snack", "mais e rosmarino", "patatine", "cracker", "grissin"
            ).any { productName.contains(it) }) return false

        if (ingredient == "limone" || ingredient == "limone amalfitano") {
            if (listOf("aroma", "estratto", "succo", "gusto limone", "al limone").any { productName.contains(it) }) return false
        }

        if (ingredient == "ricotta") {
            if (listOf("ravioli", "tortellini", "pesto", "sugo", "stagionata", "dolce", "dessert").any { productName.contains(it) }) return false
        }

        if (ingredient == "burro" && listOf(
                "biscott", "tort", "croissant", "cornett", "merend", "wafer", "froll"
            ).any { productName.contains(it) }) return false

        if (ingredient == "burro" || ingredient == "burro per besciamella") {
            if (listOf("biscott", "frollin", "torcett", "wafer", "croissant", "merend").any { productName.contains(it) }) return false
        }

        if (ingredient == "cioccolato fondente") {
            if (listOf("biscott", "wafer", "gallette", "cereali", "bar ", "protein", "snack", "frollin").any { productName.contains(it) }) return false
        }

        if (ingredient == "olive") {
            if (listOf("mix ", "insalat", "peperoni", "feta", "crostini").any { productName.contains(it) }) return false
            if (!(productName.startsWith("olive ") || productName.contains(" olive "))) return false
        }

        if (ingredient == "tonno al naturale") {
            if (!productName.contains("tonno") || !productName.contains("naturale")) return false
            if (listOf("insalatissime", "quinoa", "orzo", "farro", "ceci", "fagioli", "mais", "pasta").any { productName.contains(it) }) return false
        }

        if (ingredient == "sgombro al naturale") {
            if (!productName.contains("sgombro") || !productName.contains("naturale")) return false
            if (listOf("insalata", "olive verdi", "olive nere", "peperoncino").any { productName.contains(it) }) return false
        }

        if (ingredient == "mozzarella di bufala") {
            if (!productName.contains("mozzarella") || !productName.contains("bufala")) return false
            if (productName.contains("ricotta") || productName.contains("pesto") || productName.contains("sugo")) return false
        }

        if (ingredient == "parmigiano reggiano") {
            if (listOf("fettine", "formaggin", "crema", "cremosin", "robiol", "snack", "tortell").any { productName.contains(it) }) return false
        }

        if (ingredient == "zucchero bianco") {
            if (productName.contains("velo") || productName.contains("canna") || productName.contains("biscott")) return false
        }

        // V61 SAFETY: "farina" compare spesso nel nome di biscotti e prodotti da forno.
        // Accettiamo solo confezioni che siano realmente farina, non un alimento che la contiene.
        if (ingredient == "farina" || ingredient == "farina 00") {
            if (!productName.contains("farina")) return false
            if (listOf(
                    "biscott", "frollin", "cracker", "merend", "wafer", "tort",
                    "pane ", "panino", "pizza pronta", "pasta ", "gnocch", "grissin",
                    "mandorle", "ceci", "avena", "riso", "mais", "saraceno"
                ).any { productName.contains(it) }) return false
            if (ingredient == "farina 00" &&
                !(productName.contains("tipo 00") || productName.contains("tipo \"00\"") || productName.contains("doppio zero"))) return false
        }

        if (ingredient in setOf("mela", "pera", "pesca", "percoca", "uva")) {
            if (listOf("polpa", "frulla", "frullà", "estratto", "succo", "confettura", "snack", "mix", "sultanina", "essiccat", "disidrat").any { productName.contains(it) }) return false
        }

        if (ingredient == "sfogliatella riccia" && !productName.contains("sfogliatella riccia")) return false
        if (ingredient == "sfogliatella frolla" && !productName.contains("sfogliatella frolla")) return false
        if (ingredient == "baba pronto" && !productName.contains("baba")) return false
        if (ingredient == "pastiera pronta" && !productName.contains("pastiera")) return false
        if (ingredient == "biscotto amarena pronto" && !productName.contains("biscotto amarena")) return false
        if (ingredient == "delizia limone pronta" && !productName.contains("delizia al limone")) return false

        if (ingredient in setOf("pane", "pane raffermo", "mollica di pane")) {
            if (productName.contains("grattugiat") || productName.contains("pangratt")) return false
        }

        if (ingredient in setOf("pomodoro", "pomodori", "pomodorini", "pomodorini pizzutelli")) {
            if (listOf("passata", "pelati", "sugo", "pesto", "secchi", "conserva").any { productName.contains(it) }) return false
        }

        if (ingredient == "carciofi") {
            if (listOf("sugo", "pesto", "crema", "compresse", "arrostiti", "olio", "campagnola").any { productName.contains(it) }) return false
        }

        val rawSeafood = setOf(
            "cozze", "vongole", "vongole veraci", "polpo", "polpo verace",
            "calamari", "calamaretti", "gamberi", "gamberi o calamaretti", "scampi",
            "seppie pulite", "seppioline", "totani", "anelli di totano", "moscardini",
            "pesce spada", "filetto di pesce spada", "tonno fresco", "filetto di tonno",
            "sgombro pulito", "tranci di spigola", "orata", "filetti di orata",
            "branzino", "filetto di branzino", "alici fresche"
        )
        if (ingredient in rawSeafood && listOf(
                "burger", "bastoncini", "croccole", "pate", "paté",
                "sugo", "condimento", "insalata pronta", "piatto pronto", "pasta "
            ).any { productName.contains(it) }) return false

        // "Pane" non deve mai agganciare il marchio PANEANGELI o preparati dolci.
        if (ingredient == "pane" || ingredient == "pane raffermo" || ingredient == "mollica di pane") {
            if (productName.contains("paneangeli") ||
                productName.contains("pane angeli") ||
                productName.contains("lievito") ||
                productName.contains("preparato per dolci") ||
                productName.contains("decorazione") ||
                productName.contains("aroma")) return false
        }

        return true
    }

    private fun realisticSpecialPack(
        ingredient: String,
        pack: NormalizedQuantity
    ): Boolean {
        // Uova: una confezione reale da supermercato non è 1 uovo.
        if (ingredient == "uova" && pack.family == QuantityFamily.PIECE) {
            return pack.baseValue in 4.0..30.0
        }
        return true
    }

    private fun categoryCompatible(
        ingredient: String,
        product: SupermarketRepository.Product
    ): Boolean {
        val c = normalize(product.category.orEmpty())
        if (c.isBlank()) return true

        val market = normalize(product.supermarket)
        val isDeco = market.contains("deco")
        val isFamila = market.contains("famila")
        val isSole365 = market.contains("sole365") || market.contains("sole 365")
        val isConad = market.contains("conad")
        val isCosiComodoFood = isFamila || isSole365

        // CONAD V81: tassonomia del DB Capodrise validato. Ramo isolato:
        // non modifica Piccolo/Decò/Famila/Sole365.
        if (isConad) {
            val name = normalize(product.name)
            fun cat(vararg tokens: String): Boolean = tokens.any { token ->
                c == token || c.contains(token)
            }
            fun hasAny(vararg tokens: String): Boolean = tokens.any { name.contains(it) }

            return when {
                ingredient == "pane" || ingredient == "pane raffermo" || ingredient == "mollica di pane" ->
                    cat("panetteria e snack salati") && !hasAny("cracker", "snack", "patatine")
                ingredient == "pasta" || ingredient == "pasta corta" || ingredient == "pasta mista" ||
                    ingredient in setOf("spaghetti", "linguine", "paccheri", "mezzi paccheri", "scialatielli", "ziti", "lasagne") ->
                    cat("pasta e riso")
                ingredient == "uova" ->
                    cat("formaggi latte e uova", "preparazioni dolci e salate") &&
                        (name.contains("uova") || name.contains("uovo")) &&
                        !hasAny("albume", "tuorlo", "quaglia", "liquido", "pastorizzato")
                ingredient in setOf("mela", "pera", "pesca", "percoca", "uva", "albicocca", "susina", "melone", "anguria", "fico", "kiwi") ->
                    cat("frutta e verdura")
                ingredient == "burro" || ingredient == "burro per besciamella" -> cat("formaggi latte e uova")
                ingredient == "latte intero" ->
                    cat("formaggi latte e uova") && name.contains("latte intero") &&
                        QuantityParser.familyOf(product.quantityUnit) == QuantityFamily.VOLUME &&
                        !hasAny("yogurt", "dessert", "biscott", "merend", "mousse")
                ingredient == "latte" || ingredient.startsWith("latte ") ->
                    cat("formaggi latte e uova") && name.contains("latte") &&
                        QuantityParser.familyOf(product.quantityUnit) == QuantityFamily.VOLUME &&
                        !hasAny("yogurt", "dessert", "biscott", "merend", "burro", "panna", "uova", "mousse")
                ingredient == "ricotta" || ingredient.contains("formaggio") || ingredient.contains("parmigiano") ||
                    ingredient.contains("grana") || ingredient.contains("pecorino") || ingredient.contains("mozzarella") ||
                    ingredient.contains("provola") || ingredient.contains("fiordilatte") || ingredient.contains("caciocavallo") ->
                    cat("formaggi latte e uova")
                ingredient == "farina" || ingredient == "farina 00" -> cat("pasta e riso", "biscotti cereali e dolci", "preparazioni dolci e salate")
                ingredient == "zucchero bianco" || ingredient == "cioccolato fondente" -> cat("biscotti cereali e dolci", "condimenti e conserve")
                ingredient == "fagiolini" ->
                    cat("frutta e verdura", "surgelati e gelati") && name.contains("fagiolin")
                ingredient in setOf("pomodoro", "pomodori", "pomodorini", "pomodorini pizzutelli", "carciofi",
                    "zucchine", "melanzane", "patate", "patate a pasta gialla", "peperoni", "peperone rosso",
                    "cavolfiore", "verza", "lattuga", "friggitelli", "scarola", "friarielli",
                    "broccoli", "cicoria", "finocchi", "finocchio", "zucca", "carota", "sedano") ->
                    cat("frutta e verdura")
                ingredient == "aglio" || ingredient == "cipolla" || ingredient.startsWith("cipolla") || ingredient.startsWith("cipolle") ->
                    cat("frutta e verdura")
                ingredient == "limone" || ingredient == "limone amalfitano" -> cat("frutta e verdura")
                ingredient == "basilico" || ingredient == "prezzemolo" || ingredient == "rosmarino" ->
                    cat("frutta e verdura", "condimenti e conserve") &&
                        !hasAny("pesto", "sugo", "snack", "patatine", "gratinat", "spiedini")
                ingredient == "peperoncino" || ingredient == "peperoncino essiccato" ->
                    cat("condimenti e conserve") && name.contains("peperoncino") &&
                        !hasAny("olio", "sugo", "pesto", "snack", "crostini", "patatine")
                ingredient == "origano" ->
                    cat("condimenti e conserve") && name.contains("origano") && !hasAny("crostini", "gusto pizza")
                ingredient == "pepe" || ingredient == "pepe nero" ->
                    cat("condimenti e conserve") && hasAny("pepe nero", "pepe bianco") && !hasAny("cacio e pepe", "ricotta")
                ingredient == "sale" ->
                    cat("condimenti e conserve") && name.contains("sale") && !hasAny("primo sale", "senza sale")
                ingredient == "uvetta" -> cat("condimenti e conserve", "biscotti cereali e dolci")
                ingredient.contains("olio") ->
                    cat("condimenti e conserve") &&
                        (QuantityParser.familyOf(product.quantityUnit) == QuantityFamily.VOLUME ||
                         QuantityParser.familyOf(product.unitPriceUnit) == QuantityFamily.VOLUME)
                ingredient == "aceto" || ingredient == "aceto di vino bianco" ->
                    cat("condimenti e conserve") && name.contains("aceto")
                ingredient.contains("vino") -> cat("bevande e preparati", "vino birra e altri alcolici")
                ingredient == "alici fresche" -> cat("pesce", "surgelati e gelati")
                ingredient.contains("acciug") || ingredient.startsWith("alici") -> cat("condimenti e conserve", "pesce", "surgelati e gelati")
                ingredient in setOf("tonno al naturale", "sgombro al naturale") -> cat("condimenti e conserve", "pesce", "piatti pronti")
                ingredient in setOf("gamberi", "gamberi o calamaretti") ->
                    cat("pesce", "surgelati e gelati") &&
                        !hasAny("misto", "pastellat", "sugo", "cialde", "saikebon", "insalat")
                ingredient in setOf("calamari", "calamaretti") ->
                    cat("pesce", "surgelati e gelati") &&
                        !hasAny("misto", "pastellat", "sugo", "preparat")
                ingredient in setOf("totani", "anelli di totano") ->
                    cat("pesce", "surgelati e gelati") && !hasAny("pastellat", "misto")
                ingredient in setOf("branzino", "filetto di branzino") ->
                    cat("pesce", "surgelati e gelati") &&
                        !hasAny("con verdure", "alla ligure", "piatto pronto", "preparat")
                ingredient == "frutti di mare misti" ->
                    cat("pesce", "surgelati e gelati") &&
                        !hasAny("sugo", "zuppa", "piatto pronto", "pastellat")
                ingredient in setOf(
                    "cozze", "vongole", "vongole veraci", "polpo", "polpo verace",
                    "scampi", "seppie pulite", "seppioline", "moscardini",
                    "pesce spada", "filetto di pesce spada", "tonno fresco", "filetto di tonno",
                    "sgombro pulito", "tranci di spigola", "orata", "filetti di orata"
                ) -> cat("pesce", "surgelati e gelati")
                ingredient == "carne macinata" || ingredient.contains("manzo") || ingredient.contains("bovino") ||
                    ingredient.contains("vitello") || ingredient.contains("maiale") || ingredient.contains("salsic") ||
                    ingredient.contains("agnello") || ingredient.contains("coniglio") || ingredient.contains("trippa") ||
                    ingredient.contains("pollo") -> cat("carne e salumi")
                ingredient in setOf("ceci", "fagioli", "fagioli cannellini", "fagioli borlotti", "lenticchie") ->
                    cat("condimenti e conserve")
                else -> true
            }
        }

        // FAMILA + SOLE365: categorie ufficiali CosìComodo. Questo ramo è isolato:
        // non modifica in alcun modo le regole Piccolo/Decò già collaudate.
        if (isCosiComodoFood) {
            val name = normalize(product.name)
            fun cat(vararg tokens: String): Boolean = tokens.any { token ->
                c == token || c.contains(token)
            }
            fun hasAny(vararg tokens: String): Boolean = tokens.any { name.contains(it) }

            return when {
                ingredient == "pane" || ingredient == "pane raffermo" || ingredient == "mollica di pane" ->
                    cat("pane e pasticceria")
                ingredient == "pasta" || ingredient == "pasta corta" || ingredient == "pasta mista" ||
                    ingredient in setOf("spaghetti", "linguine", "paccheri", "mezzi paccheri", "scialatielli", "ziti", "lasagne") ->
                    cat("prodotti alimentari", "gastronomia e pasta fresca")
                ingredient == "uova" ->
                    cat("latte burro uova e yogurt") &&
                        (name.contains("uova") || name.contains("uovo")) &&
                        !hasAny("albume", "tuorlo", "quaglia", "liquido", "pastorizzato")
                ingredient in setOf("mela", "pera", "pesca", "percoca", "uva", "albicocca", "susina", "melone", "anguria", "fico", "kiwi") ->
                    cat("frutta e verdura")
                ingredient in setOf("sfogliatella riccia", "sfogliatella frolla", "baba pronto", "pastiera pronta", "biscotto amarena pronto", "delizia limone pronta") ->
                    cat("pane e pasticceria", "colazione merenda e dolci")
                ingredient == "burro" || ingredient == "burro per besciamella" ->
                    cat("latte burro uova e yogurt")
                ingredient == "latte intero" ->
                    cat("latte burro uova e yogurt") && name.contains("latte intero") &&
                        QuantityParser.familyOf(product.quantityUnit) == QuantityFamily.VOLUME &&
                        !hasAny("yogurt", "dessert", "biscott", "merend", "mousse")
                ingredient == "latte" || ingredient.startsWith("latte ") ->
                    cat("latte burro uova e yogurt") && name.contains("latte") &&
                        QuantityParser.familyOf(product.quantityUnit) == QuantityFamily.VOLUME &&
                        !hasAny("yogurt", "dessert", "biscott", "merend", "burro", "panna", "uova", "mousse")
                ingredient == "ricotta" || ingredient.contains("formaggio") || ingredient.contains("parmigiano") ||
                    ingredient.contains("grana") || ingredient.contains("pecorino") || ingredient.contains("mozzarella") ||
                    ingredient.contains("provola") || ingredient.contains("fiordilatte") || ingredient.contains("caciocavallo") ->
                    cat("salumi e formaggi")
                ingredient == "farina" || ingredient == "farina 00" || ingredient == "zucchero bianco" ||
                    ingredient == "cioccolato fondente" -> cat("prodotti alimentari", "colazione merenda e dolci")
                ingredient in setOf("pomodoro", "pomodori", "pomodorini", "pomodorini pizzutelli", "carciofi",
                    "zucchine", "melanzane", "patate", "patate a pasta gialla", "peperoni", "peperone rosso",
                    "cavolfiore", "fagiolini", "verza", "lattuga", "friggitelli", "scarola", "friarielli",
                    "broccoli", "cicoria", "finocchi", "finocchio", "zucca", "carota", "sedano") ->
                    cat("frutta e verdura")
                ingredient == "aglio" || ingredient == "cipolla" || ingredient.startsWith("cipolla") || ingredient.startsWith("cipolle") ->
                    cat("frutta e verdura")
                ingredient == "limone" || ingredient == "limone amalfitano" -> cat("frutta e verdura")
                ingredient == "basilico" ->
                    cat("frutta e verdura") || (cat("prodotti alimentari") && name.contains("basilico") && name.contains("foglie"))
                ingredient == "prezzemolo" ->
                    cat("frutta e verdura") || (cat("prodotti alimentari") && name.contains("prezzemolo") && name.contains("foglie"))
                ingredient == "rosmarino" ->
                    cat("frutta e verdura") || (cat("prodotti alimentari") && name.contains("rosmarino") && name.contains("foglie"))
                ingredient == "origano" ->                    cat("prodotti alimentari") && name.contains("origano") && !hasAny("crostini", "gusto pizza")
                ingredient == "pepe" || ingredient == "pepe nero" ->
                    cat("prodotti alimentari") && hasAny("pepe nero", "pepe bianco") && !hasAny("acini di pepe", "pepe bucato", "cacio e pepe", "ricotta")
                ingredient == "sale" -> cat("prodotti alimentari")
                ingredient == "uvetta" ->
                    cat("prodotti alimentari", "colazione merenda e dolci") &&
                        (name.startsWith("uvetta ") || name.contains("uvetta sultanina") || name.contains("uva sultanina") || name.contains("uva passa"))
                ingredient.contains("olio") ->
                    cat("prodotti alimentari") &&
                        (QuantityParser.familyOf(product.quantityUnit) == QuantityFamily.VOLUME ||
                         QuantityParser.familyOf(product.unitPriceUnit) == QuantityFamily.VOLUME)
                ingredient.contains("vino") -> cat("acqua bevande vino e alcolici")
                ingredient == "alici fresche" -> cat("pesce", "gelati e surgelati")
                ingredient.contains("acciug") || ingredient.startsWith("alici") -> cat("prodotti alimentari", "pesce", "gelati e surgelati")
                ingredient in setOf("tonno al naturale", "sgombro al naturale") -> cat("prodotti alimentari")
                ingredient in setOf(
                    "cozze", "vongole", "vongole veraci", "polpo", "polpo verace",
                    "calamari", "calamaretti", "gamberi", "gamberi o calamaretti", "scampi",
                    "seppie pulite", "seppioline", "totani", "anelli di totano", "moscardini",
                    "pesce spada", "filetto di pesce spada", "tonno fresco", "filetto di tonno",
                    "sgombro pulito", "tranci di spigola", "orata", "filetti di orata",
                    "branzino", "filetto di branzino"
                ) -> cat("pesce", "gelati e surgelati")
                ingredient == "carne macinata" || ingredient.contains("manzo") || ingredient.contains("bovino") ||
                    ingredient.contains("vitello") || ingredient.contains("maiale") || ingredient.contains("salsic") ||
                    ingredient.contains("agnello") || ingredient.contains("coniglio") || ingredient.contains("trippa") ||
                    ingredient.contains("pollo") -> cat("carne")
                else -> true
            }
        }

        // PICCOLO: logica V64 invariata byte-per-byte nei criteri.
        if (!isDeco) {
            return when {
                ingredient == "pane" || ingredient == "pane raffermo" || ingredient == "mollica di pane" ->
                    c == "pane" || c == "pasta pane farinacei"
                ingredient == "pasta" || ingredient == "pasta corta" || ingredient == "pasta mista" ||
                    ingredient in setOf("spaghetti", "linguine", "paccheri", "mezzi paccheri", "scialatielli", "ziti", "lasagne") ->
                    c == "pasta pane farinacei"
                ingredient == "uova" -> c == "uova"
                ingredient in setOf("mela", "pera", "pesca", "percoca", "uva") -> c == "frutta"
                ingredient in setOf("sfogliatella riccia", "sfogliatella frolla", "baba pronto", "pastiera pronta", "biscotto amarena pronto", "delizia limone pronta") -> c == "dolci dispensa"
                ingredient == "burro" || ingredient == "burro per besciamella" -> c == "formaggi"
                ingredient == "cioccolato fondente" -> c == "dispensa scatolame"
                ingredient == "zucchero bianco" -> c == "condimenti"
                ingredient == "farina" || ingredient == "farina 00" -> c == "dispensa scatolame"
                ingredient in setOf("pomodoro", "pomodori", "pomodorini", "pomodorini pizzutelli") ->
                    c == "verdura" || c == "verdura legumi cereali"
                ingredient == "carciofi" -> c == "verdura" || c == "verdura legumi cereali"
                ingredient == "sale" || ingredient == "pepe" -> c == "condimenti"
                ingredient == "aglio" -> c == "verdura legumi cereali" || c == "verdura"
                ingredient == "limone" || ingredient == "limone amalfitano" -> c == "frutta"
                ingredient == "cipolla" || ingredient.startsWith("cipolla") || ingredient.startsWith("cipolle") ->
                    c == "verdura legumi cereali" || c == "verdura"
                ingredient.contains("olio") -> c == "olio" || c == "condimenti"
                ingredient.contains("vino") -> c.contains("vino") || c.contains("bevande")
                ingredient == "latte" || ingredient == "latte intero" || ingredient.startsWith("latte ") -> c == "latte"
                ingredient == "burro" -> c == "formaggi" || c == "latte"
                ingredient == "ricotta" -> c == "formaggi"
                ingredient.contains("formaggio") || ingredient.contains("parmigiano") ||
                    ingredient.contains("grana") || ingredient.contains("pecorino") ||
                    ingredient.contains("mozzarella") || ingredient.contains("provola") ||
                    ingredient.contains("fiordilatte") || ingredient.contains("caciocavallo") -> c == "formaggi"
                ingredient == "alici fresche" -> c == "pesce surgelato"
                ingredient.contains("acciug") || ingredient.startsWith("alici") ->
                    c in setOf("pesce scatola", "dispensa scatolame", "pesce surgelato")
                ingredient in setOf("tonno al naturale", "sgombro al naturale") -> c == "pesce scatola"
                ingredient in setOf(
                    "cozze", "vongole", "vongole veraci", "polpo", "polpo verace",
                    "calamari", "calamaretti", "gamberi", "gamberi o calamaretti", "scampi",
                    "seppie pulite", "seppioline", "totani", "anelli di totano", "moscardini",
                    "pesce spada", "filetto di pesce spada", "tonno fresco", "filetto di tonno",
                    "sgombro pulito", "tranci di spigola", "orata", "filetti di orata",
                    "branzino", "filetto di branzino"
                ) -> c == "pesce surgelato"
                ingredient == "carne macinata" || ingredient.contains("manzo") || ingredient.contains("bovino") || ingredient.contains("vitello") ||
                    ingredient.contains("maiale") || ingredient.contains("salsic") || ingredient.contains("agnello") ||
                    ingredient.contains("coniglio") || ingredient.contains("trippa") -> c == "carne"
                ingredient.contains("mozzarella") || ingredient.contains("provola") || ingredient.contains("fiordilatte") ||
                    ingredient.contains("parmigiano") || ingredient.contains("pecorino") || ingredient.contains("caciocavallo") -> c == "formaggi"
                else -> true
            }
        }

        // DECÒ: stesse barriere semantiche, categorie ufficiali aggregate Decò.
        fun cat(vararg tokens: String): Boolean = tokens.any { token ->
            c == token || c.contains(token)
        }

        return when {
            ingredient == "pane" || ingredient == "pane raffermo" || ingredient == "mollica di pane" ->
                cat("pasta pane e farinacei")
            ingredient == "pasta" || ingredient == "pasta corta" || ingredient == "pasta mista" ||
                ingredient in setOf("spaghetti", "linguine", "paccheri", "mezzi paccheri", "scialatielli", "ziti", "lasagne") ->
                cat("pasta pane e farinacei")
            ingredient == "uova" -> cat("latte burro uova")
            ingredient in setOf("mela", "pera", "pesca", "percoca", "uva") -> cat("frutta")
            ingredient in setOf("sfogliatella riccia", "sfogliatella frolla", "baba pronto", "pastiera pronta", "biscotto amarena pronto", "delizia limone pronta") ->
                cat("dolci")
            ingredient == "burro" || ingredient == "burro per besciamella" -> cat("latte burro uova")
            ingredient == "cioccolato fondente" -> cat("dolci", "snack dolci", "dispensa scatolame")
            ingredient == "zucchero bianco" -> cat("dolci", "dispensa scatolame", "pasta pane e farinacei")
            ingredient == "farina" || ingredient == "farina 00" -> cat("pasta pane e farinacei", "dolci")
            ingredient in setOf("pomodoro", "pomodori", "pomodorini", "pomodorini pizzutelli") -> cat("verdura legumi e cereali")
            ingredient == "carciofi" -> cat("verdura legumi e cereali")
            ingredient == "sale" || ingredient == "pepe" -> cat("condimenti")
            ingredient == "aglio" -> cat("verdura legumi e cereali")
            ingredient == "limone" || ingredient == "limone amalfitano" -> cat("frutta")
            ingredient == "cipolla" || ingredient.startsWith("cipolla") || ingredient.startsWith("cipolle") -> cat("verdura legumi e cereali")
            ingredient.contains("olio") -> cat("olio", "condimenti")
            ingredient.contains("vino") -> cat("vino", "bibite")
            ingredient == "latte" || ingredient == "latte intero" || ingredient.startsWith("latte ") -> cat("latte burro uova")
            ingredient == "ricotta" -> cat("formaggi")
            ingredient.contains("formaggio") || ingredient.contains("parmigiano") || ingredient.contains("grana") ||
                ingredient.contains("pecorino") || ingredient.contains("mozzarella") || ingredient.contains("provola") ||
                ingredient.contains("fiordilatte") || ingredient.contains("caciocavallo") -> cat("formaggi")
            ingredient == "alici fresche" -> cat("pesce fresco", "surgelati")
            ingredient.contains("acciug") || ingredient.startsWith("alici") -> cat("dispensa scatolame", "surgelati", "pesce fresco")
            ingredient in setOf("tonno al naturale", "sgombro al naturale") -> cat("dispensa scatolame")
            ingredient in setOf(
                "cozze", "vongole", "vongole veraci", "polpo", "polpo verace",
                "calamari", "calamaretti", "gamberi", "gamberi o calamaretti", "scampi",
                "seppie pulite", "seppioline", "totani", "anelli di totano", "moscardini",
                "pesce spada", "filetto di pesce spada", "tonno fresco", "filetto di tonno",
                "sgombro pulito", "tranci di spigola", "orata", "filetti di orata",
                "branzino", "filetto di branzino"
            ) -> cat("pesce fresco", "surgelati")
            ingredient == "carne macinata" || ingredient.contains("manzo") || ingredient.contains("bovino") || ingredient.contains("vitello") ||
                ingredient.contains("maiale") || ingredient.contains("salsic") || ingredient.contains("agnello") ||
                ingredient.contains("coniglio") || ingredient.contains("trippa") -> cat("carne")
            else -> true
        }
    }

    private fun realisticProductForIngredient(
        ingredient: String,
        product: SupermarketRepository.Product,
        pack: NormalizedQuantity
    ): Boolean {
        val name = normalize(product.name)

        if (ingredient == "sale") {
            if (pack.family != QuantityFamily.MASS) return false
            if (pack.baseValue < 500.0 || pack.baseValue > 2000.0) return false

            val euroPerKg = when {
                product.unitPriceEur != null &&
                    normalize(product.unitPriceUnit.orEmpty()) == "kg" ->
                    product.unitPriceEur

                pack.baseValue > 0.0 ->
                    product.priceEur / (pack.baseValue / 1000.0)

                else -> null
            }

            if (euroPerKg != null && euroPerKg > 5.0) return false
            if (listOf(
                    "himalaya", "rosa", "maldon", "fiocchi", "affumicato",
                    "aromatizzato", "gourmet", "nero di", "blu di"
                ).any { name.contains(it) }) return false
        }

        if ((ingredient == "pane" || ingredient == "pane raffermo" || ingredient == "mollica di pane") &&
            (name.contains("paneangeli") || name.contains("pane angeli"))) {
            return false
        }

        return true
    }

    /**
     * V45 — indice lessicale del catalogo Piccolo.
     *
     * Prima ogni nuovo ingrediente scansionava tutti i ~4.000 prodotti.
     * Ora l'indice viene costruito una sola volta e il matching parte da una
     * piccola lista di prodotti che contiene almeno una parola significativa
     * dell'ingrediente/sinonimo. Il controllo semantico rigoroso resta identico.
     */
    fun prepare(products: List<SupermarketRepository.Product>) {
        productIndex(products)
    }

    private fun productIndex(
        products: List<SupermarketRepository.Product>
    ): ProductTokenIndex {
        val indexKey = "${System.identityHashCode(products)}|${products.size}"
        return productIndexCache.getOrPut(indexKey) {
            if (productIndexCache.size > 4) productIndexCache.clear()

            val temp = HashMap<String, MutableList<SupermarketRepository.Product>>()
            products.forEach { product ->
                if (product.priceEur <= 0.0) return@forEach
                val words = prepared(product).name
                    .split(' ')
                    .asSequence()
                    .filter { it.length >= 3 }
                    .distinct()
                words.forEach { token ->
                    temp.getOrPut(token) { mutableListOf() }.add(product)
                }
            }
            ProductTokenIndex(
                byToken = temp.mapValues { (_, value) -> value.toList() }
            )
        }
    }

    private fun significantToken(phrase: String): String? {
        val stop = setOf("del", "della", "delle", "degli", "dei", "con", "per", "alla", "alle", "al", "di")
        return normalize(phrase)
            .split(' ')
            .filter { it.length >= 3 && it !in stop }
            .maxByOrNull { it.length }
    }

    private fun candidateProducts(
        ingredient: String,
        phrases: Set<String>,
        products: List<SupermarketRepository.Product>
    ): List<SupermarketRepository.Product> {
        if (candidateCache.size > 512) candidateCache.clear()

        val key = "${System.identityHashCode(products)}|${products.size}|$ingredient"
        return candidateCache.getOrPut(key) {
            val idx = productIndex(products)
            val preselected = LinkedHashMap<String, SupermarketRepository.Product>()

            phrases.forEach { phrase ->
                val token = significantToken(phrase)
                if (token != null) {
                    idx.byToken[token].orEmpty().forEach { preselected[it.key] = it }
                }
            }

            // Fail-safe: per nomi molto corti/atipici manteniamo la scansione
            // completa, ma nella pratica quasi tutti gli ingredienti passano
            // dall'indice e la generazione del menu diventa molto più rapida.
            val source = if (preselected.isNotEmpty()) preselected.values else products

            source.filter { product ->
                if (product.priceEur <= 0.0) return@filter false
                val pp = prepared(product)
                strictSemanticMatch(
                    ingredient = ingredient,
                    phrases = phrases,
                    productName = pp.name,
                    paddedProductName = pp.paddedName
                ) && categoryCompatible(ingredient, product)
            }
        }
    }

    /**
     * V47 — pasta mista: Piccolo ha prodotti reali espliciti (es.
     * "PICCOLO PASTA MISTA 500 GR"). Se per qualche snapshot il nome esatto
     * non viene indicizzato, usiamo solo come fallback formati corti da minestra
     * chiaramente identificabili, mai una "pasta generica" anonima.
     */
    private fun resolvedCandidates(
        ingredient: String,
        phrases: Set<String>,
        products: List<SupermarketRepository.Product>
    ): List<SupermarketRepository.Product> {
        val primary = candidateProducts(ingredient, phrases, products)
        if (primary.isNotEmpty() || ingredient != "pasta mista") return primary

        val fallbackTerms = listOf("ditaloni", "lumachine", "tubetti", "paternosti")
        return products.asSequence()
            .filter { it.priceEur > 0.0 }
            .filter { product ->
                val c = normalize(product.category.orEmpty())
                c.isBlank() || c == "pasta pane farinacei" || c == "pasta pane e farinacei" || c == "prodotti alimentari" || c == "gastronomia e pasta fresca"
            }
            .filter { product ->
                val name = normalize(product.name)
                fallbackTerms.any { term -> name.contains(term) }
            }
            .toList()
    }

    private fun reasonablePack(
        ingredient: String,
        needBase: Double,
        pack: NormalizedQuantity
    ): Boolean {
        val absoluteMax = absoluteHouseholdMax(ingredient, pack.family)
        if (pack.baseValue > absoluteMax) return false

        // V44: normali confezioni da supermercato non devono essere scartate
        // solo perché la ricetta ne usa una piccola quantità.
        if (
            pack.family == QuantityFamily.VOLUME &&
            (ingredient == "latte" || ingredient == "latte intero" || ingredient.startsWith("latte "))
        ) {
            return pack.baseValue in 250.0..1500.0
        }

        if (pack.family == QuantityFamily.VOLUME && ingredient.contains("vino")) {
            return pack.baseValue in 250.0..1500.0
        }

        if (pack.family == QuantityFamily.VOLUME && ingredient.contains("aceto")) {
            return pack.baseValue in 100.0..2000.0
        }

        if (pack.family == QuantityFamily.MASS && ingredient == "sale") {
            return pack.baseValue in 500.0..2000.0
        }

        if (pack.family == QuantityFamily.MASS && (ingredient == "farina" || ingredient == "farina 00")) {
            return pack.baseValue in 500.0..2000.0
        }

        if (pack.family == QuantityFamily.VOLUME &&
            (ingredient.contains("olio per friggere") || ingredient.contains("olio di semi"))) {
            return pack.baseValue in 750.0..2000.0
        }

        // Olio EVO domestico: niente micro-bottiglie da 100/200 ml.
        if (pack.family == QuantityFamily.VOLUME && ingredient.contains("olio extravergine")) {
            return pack.baseValue in 500.0..2000.0
        }

        /*
         * Una persona non compra normalmente una confezione 40-100 volte
         * più grande del fabbisogno se esistono alternative.
         * Usiamo un tetto largo per non scartare normali confezioni.
         */
        val ratioLimit = when {
            isSpiceOrCondiment(ingredient) -> 80.0
            needBase < 50.0 -> 30.0
            needBase < 250.0 -> 15.0
            else -> 10.0
        }

        return pack.baseValue <= needBase * ratioLimit
    }

    private fun reasonableUnknownNeedPack(
        ingredient: String,
        pack: NormalizedQuantity
    ): Boolean {
        if (pack.baseValue > absoluteHouseholdMax(ingredient, pack.family)) return false

        // V38: evita micro-confezioni poco rappresentative quando la ricetta
        // richiede un normale prodotto da dispensa.
        if (ingredient.contains("olio") && pack.family == QuantityFamily.VOLUME) {
            return pack.baseValue >= 500.0
        }

        return true
    }

    private fun householdPackPenalty(
        ingredient: String,
        pack: NormalizedQuantity,
        needBase: Double
    ): Double {
        val target = when {
            ingredient.contains("olio") && pack.family == QuantityFamily.VOLUME -> 1000.0
            ingredient == "sale" && pack.family == QuantityFamily.MASS -> 1000.0
            ingredient.contains("patate") && pack.family == QuantityFamily.MASS -> maxOf(needBase, 1000.0)
            ingredient.contains("melanzan") && pack.family == QuantityFamily.MASS -> maxOf(needBase, 500.0)
            ingredient.contains("mozzarella") || ingredient.contains("provola") || ingredient.contains("fiordilatte") -> maxOf(needBase, 250.0)
            ingredient.contains("macinat") && pack.family == QuantityFamily.MASS -> maxOf(needBase, 400.0)
            ingredient == "uova" && pack.family == QuantityFamily.PIECE -> maxOf(needBase, 6.0)
            ingredient == "pane" && pack.family == QuantityFamily.MASS -> maxOf(needBase, 400.0)
            ingredient.contains("vino") && pack.family == QuantityFamily.VOLUME -> 750.0
            else -> needBase.coerceAtLeast(1.0)
        }

        return kotlin.math.abs(pack.baseValue - target) / target
    }

    private fun absoluteHouseholdMax(
        ingredient: String,
        family: QuantityFamily
    ): Double {
        return when (family) {
            QuantityFamily.MASS -> when {
                ingredient.contains("acciug") ||
                    ingredient.contains("alice") ||
                    ingredient.contains("alici") -> 500.0
                ingredient in setOf(
                    "capperi", "pepe", "origano", "basilico",
                    "prezzemolo", "rosmarino", "menta", "noce moscata"
                ) -> 300.0
                ingredient.contains("pinoli") ||
                    ingredient.contains("uvetta") ||
                    ingredient.contains("uva passa") -> 500.0
                else -> 5000.0
            }
            QuantityFamily.VOLUME -> when {
                ingredient.contains("olio") -> 2000.0
                ingredient.contains("aceto") -> 2000.0
                ingredient.contains("vino") -> 2000.0
                else -> 5000.0
            }
            QuantityFamily.PIECE -> 30.0
            QuantityFamily.UNKNOWN -> Double.POSITIVE_INFINITY
        }
    }

    private fun isSpiceOrCondiment(ingredient: String): Boolean =
        ingredient in setOf(
            "sale", "pepe", "capperi", "basilico", "prezzemolo",
            "origano", "rosmarino", "menta", "peperoncino",
            "noce moscata"
        )

    private fun prepared(product: SupermarketRepository.Product): PreparedProduct {
        return preparedCache.getOrPut(product.key) {
            val n = normalize(product.name)
            PreparedProduct(
                name = n,
                paddedName = " $n "
            )
        }
    }

    private fun containsPhrase(paddedText: String, phrase: String): Boolean {
        val p = normalize(phrase)
        if (p.isBlank()) return false
        return paddedText.contains(" $p ")
    }

    internal fun normalize(value: String): String {
        normalizeCache[value]?.let { return it }

        val noAccent = Normalizer
            .normalize(value.lowercase(), Normalizer.Form.NFD)
            .replace(Regex("\\p{M}+"), "")

        val normalized = noAccent
            .replace("’", "'")
            .replace("'", " ")
            .replace(Regex("[^a-z0-9]+"), " ")
            .trim()
            .replace(Regex("\\s+"), " ")

        if (normalizeCache.size > 12_000) normalizeCache.clear()
        normalizeCache[value] = normalized
        return normalized
    }
}