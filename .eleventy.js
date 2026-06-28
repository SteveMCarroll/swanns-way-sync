export default function (eleventyConfig) {
  eleventyConfig.addPassthroughCopy({ "src/css": "css" });
  eleventyConfig.addPassthroughCopy({ "correspondence.csv": "correspondence.csv" });

  eleventyConfig.addGlobalData("site", {
    title: "Swann's Way \u2014 Reading Sync",
    description:
      "Landmark guideposts linking the Davis (Penguin) and Moncrieff (Modern Library) editions of Swann's Way to the audiobook, so you can switch between paper, ebook, and audio.",
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

  return {
    dir: { input: "src", includes: "_includes", data: "_data", output: "_site" },
    markdownTemplateEngine: "njk",
    htmlTemplateEngine: "njk",
    templateFormats: ["md", "njk", "html"],
    pathPrefix: process.env.PATH_PREFIX || "/",
  };
}
