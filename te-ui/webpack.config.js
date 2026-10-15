// Single-page app: src/index.tsx -> dist/ (served by nginx in the container).
const path = require("path");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");

module.exports = (_env, argv) => {
  const prod = argv.mode === "production";
  return {
    entry: "./src/index.tsx",
    output: { path: path.resolve(__dirname, "dist"), filename: prod ? "app.[contenthash:8].js" : "app.js", clean: true },
    resolve: { extensions: [".tsx", ".ts", ".js"] },
    module: {
      rules: [
        { test: /\.[jt]sx?$/, exclude: /node_modules/, use: "babel-loader" },
        { test: /\.css$/, use: [prod ? MiniCssExtractPlugin.loader : "style-loader", "css-loader"] },
      ],
    },
    plugins: [
      new HtmlWebpackPlugin({ template: "./public/index.html" }),
      ...(prod ? [new MiniCssExtractPlugin({ filename: "app.[contenthash:8].css" })] : []),
    ],
    devtool: prod ? "source-map" : "eval-cheap-module-source-map",
    // `npm start` outside Docker: proxy /v1 to te-api like nginx does in the container.
    devServer: {
      port: 3000,
      historyApiFallback: true,
      proxy: [
        { context: ["/v1"], target: process.env.API_URL || "http://localhost:8080" },
      ],
    },
  };
};
