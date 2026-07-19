export default function (eleventyConfig) {
  eleventyConfig.addPassthroughCopy({ "src/css": "css" });
  eleventyConfig.addPassthroughCopy({ "correspondence.csv": "correspondence.csv" });
  eleventyConfig.addPassthroughCopy({ "correspondence_bg.csv": "correspondence_bg.csv" });

  eleventyConfig.addGlobalData("site", {
    title: "Proust \u2014 Reading Sync",
    description:
      "Landmark guideposts linking the Moncrieff (Modern Library) and Davis (Penguin) editions of Proust's novel to the audiobook, so you can switch between paper, ebook, and audio.",
  });

  eleventyConfig.addFilter("groupByPart", (landmarks) => {
    const order = ["Combray", "Swann in Love", "Place-Names"];
    const groups = new Map();
    for (const lm of landmarks ?? []) {
      if (!groups.has(lm.part)) groups.set(lm.part, []);
      groups.get(lm.part).push(lm);
    }
    return order
      .filter((p) => groups.has(p))
      .map((p) => ({ part: p, items: groups.get(p) }));
  });

  eleventyConfig.addFilter("groupBySection", (landmarks) => {
    const order = [
      "Madame Swann at Home",
      "Place-Names: The Place",
      "Seascape, with Frieze of Girls",
    ];
    const groups = new Map();
    for (const lm of landmarks ?? []) {
      if (!groups.has(lm.section)) groups.set(lm.section, []);
      groups.get(lm.section).push(lm);
    }
    return order
      .filter((s) => groups.has(s))
      .map((s) => ({ part: s, items: groups.get(s) }));
  });

  return {
    dir: { input: "src", includes: "_includes", data: "_data", output: "_site" },
    markdownTemplateEngine: "njk",
    htmlTemplateEngine: "njk",
    templateFormats: ["md", "njk", "html"],
    pathPrefix: process.env.PATH_PREFIX || "/",
  };
}
