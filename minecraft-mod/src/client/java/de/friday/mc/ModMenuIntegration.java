package de.friday.mc;

import com.terraformersmc.modmenu.api.ConfigScreenFactory;
import com.terraformersmc.modmenu.api.ModMenuApi;
import me.shedaniel.clothconfig2.api.ConfigBuilder;
import me.shedaniel.clothconfig2.api.ConfigCategory;
import me.shedaniel.clothconfig2.api.ConfigEntryBuilder;
import net.minecraft.network.chat.Component;

/** Einstellungen der Mod im Mod-Menü (braucht Mod Menu und Cloth Config, beide hier installiert). */
public class ModMenuIntegration implements ModMenuApi {
	@Override
	public ConfigScreenFactory<?> getModConfigScreenFactory() {
		return parent -> {
			FridayKarte.Konfig k = FridayKarte.konfig();
			ConfigBuilder b = ConfigBuilder.create().setParentScreen(parent).setTitle(Component.literal("Friday Karte"));
			b.setSavingRunnable(FridayKarte::speichern);
			ConfigCategory c = b.getOrCreateCategory(Component.literal("Verbindung"));
			ConfigEntryBuilder e = b.entryBuilder();
			c.addEntry(e.startBooleanToggle(Component.literal("Spielstand senden"), k.aktiv)
				.setDefaultValue(true).setSaveConsumer(v -> k.aktiv = v).build());
			c.addEntry(e.startIntSlider(Component.literal("Alle … Sekunden senden"), k.intervall, 5, 300)
				.setDefaultValue(30).setTextGetter(v -> Component.literal(v + " s")).setSaveConsumer(v -> k.intervall = v).build());
			c.addEntry(e.startStrField(Component.literal("Adresse von Friday"), k.url)
				.setDefaultValue("https://hphome.beispiel.ts.net/api/minecraft").setSaveConsumer(v -> k.url = v.trim()).build());
			c.addEntry(e.startStrField(Component.literal("Schlüssel"), k.token)
				.setSaveConsumer(v -> k.token = v.trim()).build());
			return b.build();
		};
	}
}
