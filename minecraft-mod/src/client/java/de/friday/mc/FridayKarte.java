package de.friday.mc;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.brigadier.arguments.IntegerArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.command.v2.ClientCommands;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;
import net.minecraft.client.Minecraft;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;

/**
 * Schickt den Spielstand alle paar Sekunden an das Friday-Dashboard.
 * Nur lesen, nichts am Spiel ändern. Einstellungen: config/friday-karte.json,
 * im Spiel per /fridaykarte (intervall, url, token, test).
 */
public class FridayKarte implements ClientModInitializer {
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
	private static final Path DATEI = FabricLoader.getInstance().getConfigDir().resolve("friday-karte.json");
	private static final HttpClient HTTP = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();

	private static Konfig konfig = new Konfig();
	private static int zaehler = 0;
	private static String letzterFehler = "";

	public static class Konfig {
		public String url = "https://hphome.beispiel.ts.net/api/minecraft";
		public String token = "";
		public int intervall = 30;       // Sekunden, 5 bis 300
		public boolean aktiv = true;
	}

	public static Konfig konfig() { return konfig; }

	@Override
	public void onInitializeClient() {
		laden();
		ClientTickEvents.END_CLIENT_TICK.register(mc -> {
			if (!konfig.aktiv || ++zaehler < Math.max(5, konfig.intervall) * 20) return;
			zaehler = 0;
			senden(mc, false);
		});
		ClientLifecycleEvents.CLIENT_STOPPING.register(mc -> {
			try { post(statusAus(), 1500); } catch (Exception ignoriert) { }
		});
		ClientCommandRegistrationCallback.EVENT.register((dispatcher, ctx) -> dispatcher.register(
			ClientCommands.literal("fridaykarte")
				.then(ClientCommands.literal("intervall").then(ClientCommands.argument("sekunden", IntegerArgumentType.integer(5, 300)).executes(c -> {
					konfig.intervall = IntegerArgumentType.getInteger(c, "sekunden");
					speichern();
					meldung("Friday-Karte: alle " + konfig.intervall + " s");
					return 1;
				})))
				.then(ClientCommands.literal("url").then(ClientCommands.argument("adresse", StringArgumentType.string()).executes(c -> {
					konfig.url = StringArgumentType.getString(c, "adresse");
					speichern();
					meldung("Friday-Karte: Adresse gespeichert");
					return 1;
				})))
				.then(ClientCommands.literal("token").then(ClientCommands.argument("wert", StringArgumentType.string()).executes(c -> {
					konfig.token = StringArgumentType.getString(c, "wert");
					speichern();
					meldung("Friday-Karte: Schlüssel gespeichert");
					return 1;
				})))
				.then(ClientCommands.literal("test").executes(c -> {
					senden(Minecraft.getInstance(), true);
					return 1;
				}))
				.executes(c -> {
					meldung("Friday-Karte: alle " + konfig.intervall + " s an " + konfig.url + (konfig.token.isEmpty() ? " (Schlüssel fehlt!)" : "")
						+ (letzterFehler.isEmpty() ? "" : " · letzter Fehler: " + letzterFehler));
					return 1;
				})));
	}

	private static void meldung(String text) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.player != null) mc.player.sendSystemMessage(Component.literal(text));
	}

	private static void laden() {
		try {
			if (Files.exists(DATEI)) konfig = GSON.fromJson(Files.readString(DATEI), Konfig.class);
			else speichern();
		} catch (Exception e) { konfig = new Konfig(); }
	}

	public static void speichern() {
		try { Files.writeString(DATEI, GSON.toJson(konfig)); } catch (Exception ignoriert) { }
	}

	// ---------- Daten sammeln ----------

	private static JsonObject basis(String zustand) {
		JsonObject o = new JsonObject();
		o.addProperty("zustand", zustand);
		o.addProperty("version", net.minecraft.SharedConstants.getCurrentVersion().name());
		o.addProperty("loader", "Fabric");
		o.addProperty("loader_version", FabricLoader.getInstance().getModContainer("fabricloader").map(m -> m.getMetadata().getVersion().getFriendlyString()).orElse(""));
		int mods = 0;
		for (ModContainer m : FabricLoader.getInstance().getAllMods())
			if (m.getContainingMod().isEmpty() && m.getOrigin().getKind() != net.fabricmc.loader.api.metadata.ModOrigin.Kind.UNKNOWN
				&& !m.getMetadata().getId().equals("java") && !m.getMetadata().getId().equals("minecraft")
				&& !m.getMetadata().getType().equals("builtin")) mods++;
		o.addProperty("mods", mods);
		return o;
	}

	private static JsonObject statusAus() { return basis("aus"); }

	private static String kennung(ItemStack s) {
		return s.isEmpty() ? "" : BuiltInRegistries.ITEM.getKey(s.getItem()).getPath();
	}

	private static JsonObject stapel(ItemStack s) {
		JsonObject o = new JsonObject();
		o.addProperty("id", kennung(s));
		o.addProperty("anzahl", s.getCount());
		return o;
	}

	static JsonObject sammeln(Minecraft mc) {
		Player p = mc.player;
		if (p == null || mc.level == null) return basis("menu");
		boolean server = mc.getCurrentServer() != null && !mc.hasSingleplayerServer();
		JsonObject o = basis(server ? "server" : "welt");
		if (server) {
			o.addProperty("name", mc.getCurrentServer().name);
			o.addProperty("adresse", mc.getCurrentServer().ip);
		} else if (mc.getSingleplayerServer() != null) {
			o.addProperty("name", mc.getSingleplayerServer().getWorldData().getLevelName());
		}
		o.addProperty("dimension", mc.level.dimension().identifier().getPath());
		o.addProperty("x", (int) Math.floor(p.getX()));
		o.addProperty("y", (int) Math.floor(p.getY()));
		o.addProperty("z", (int) Math.floor(p.getZ()));
		o.addProperty("tageszeit", mc.level.getOverworldClockTime() % 24000L);
		o.addProperty("tag", mc.level.getOverworldClockTime() / 24000L);
		o.addProperty("leben", p.getHealth());
		o.addProperty("leben_max", p.getMaxHealth());
		o.addProperty("absorption", p.getAbsorptionAmount());
		o.addProperty("hunger", p.getFoodData().getFoodLevel());
		o.addProperty("ruestung_wert", p.getArmorValue());
		o.addProperty("erfahrung", p.experienceLevel);

		JsonObject r = new JsonObject();
		r.addProperty("kopf", kennung(p.getItemBySlot(EquipmentSlot.HEAD)));
		r.addProperty("brust", kennung(p.getItemBySlot(EquipmentSlot.CHEST)));
		r.addProperty("beine", kennung(p.getItemBySlot(EquipmentSlot.LEGS)));
		r.addProperty("fuesse", kennung(p.getItemBySlot(EquipmentSlot.FEET)));
		o.add("ruestung", r);

		Inventory inv = p.getInventory();
		JsonArray hot = new JsonArray();
		for (int i = 0; i < 9; i++) hot.add(stapel(inv.getItem(i)));
		o.add("hotbar", hot);
		JsonArray rucksack = new JsonArray();
		for (int i = 9; i < 36; i++) rucksack.add(stapel(inv.getItem(i)));
		o.add("inventar", rucksack);
		o.addProperty("auswahl", inv.getSelectedSlot());
		o.add("hand", stapel(p.getMainHandItem()));
		o.add("nebenhand", stapel(p.getOffhandItem()));

		var skin = mc.player.getSkin();
		o.addProperty("skin_url", skin.body() instanceof net.minecraft.core.ClientAsset.DownloadedTexture t && t.url() != null ? t.url() : "");
		o.addProperty("skin_pfad", skin.body().texturePath().getPath());
		String png = skinPng(skin.body().texturePath().getPath());
		if (png != null) o.addProperty("skin_png", png);
		o.addProperty("skin_schmal", skin.model() == net.minecraft.world.entity.player.PlayerModelType.SLIM);
		return o;
	}

	// Eigene Skins liegen als Datei im Asset-Ordner des Launchers (assets/skins/<2 Zeichen>/<Hash>); Standardskins nicht.
	private static String skinCacheSchluessel = "";
	private static String skinCachePng = null;

	private static String skinPng(String pfad) {
		if (!pfad.startsWith("skins/")) return null;
		if (pfad.equals(skinCacheSchluessel)) return skinCachePng;
		skinCacheSchluessel = pfad;
		skinCachePng = null;
		try {
			String hash = pfad.substring("skins/".length());
			String[] args = FabricLoader.getInstance().getLaunchArguments(false);
			for (int i = 0; i + 1 < args.length; i++) {
				if (!args[i].equals("--assetsDir")) continue;
				Path datei = Path.of(args[i + 1], "skins", hash.substring(0, 2), hash);
				if (Files.isRegularFile(datei) && Files.size(datei) < 65536)
					skinCachePng = java.util.Base64.getEncoder().encodeToString(Files.readAllBytes(datei));
			}
		} catch (Exception ignoriert) { }
		return skinCachePng;
	}

	// ---------- Senden ----------

	private static void senden(Minecraft mc, boolean melden) {
		if (!konfig.aktiv && !melden) return;
		if (konfig.token.isEmpty()) {
			if (melden) meldung("Friday-Karte: erst /fridaykarte token <wert> setzen");
			return;
		}
		JsonObject daten = sammeln(mc);
		Thread.ofVirtual().start(() -> {
			try {
				post(daten, 4000);
				letzterFehler = "";
				if (melden) mc.execute(() -> meldung("Friday-Karte: gesendet"));
			} catch (Exception e) {
				letzterFehler = String.valueOf(e.getMessage());
				if (melden) mc.execute(() -> meldung("Friday-Karte: Fehler " + letzterFehler));
			}
		});
	}

	private static void post(JsonObject daten, int zeitMs) throws Exception {
		HttpRequest req = HttpRequest.newBuilder(URI.create(konfig.url))
			.timeout(Duration.ofMillis(zeitMs))
			.header("Content-Type", "application/json")
			.header("Authorization", "Bearer " + konfig.token)
			.POST(HttpRequest.BodyPublishers.ofString(daten.toString())).build();
		HttpResponse<String> a = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
		if (a.statusCode() >= 300) throw new RuntimeException("HTTP " + a.statusCode());
	}
}
