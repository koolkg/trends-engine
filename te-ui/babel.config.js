// Jest (NODE_ENV=test) compiles for the running node; webpack compiles for browsers.
module.exports = (api) => {
  const test = api.env("test");
  return {
    presets: [
      ["@babel/preset-env", { targets: test ? { node: "current" } : "defaults" }],
      ["@babel/preset-react", { runtime: "automatic" }],
      "@babel/preset-typescript",
    ],
  };
};
