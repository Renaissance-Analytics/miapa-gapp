declare const __MAIPA_BUILD_ID__: string;
declare const __MAIPA_RELEASE_LABEL__: string;
declare const __MAIPA_UPDATES_ENABLED__: boolean;

export const BUILD_ID = typeof __MAIPA_BUILD_ID__ === "undefined" ? "unbuilt" : __MAIPA_BUILD_ID__;
export const RELEASE_LABEL = typeof __MAIPA_RELEASE_LABEL__ === "undefined" ? "Development" : __MAIPA_RELEASE_LABEL__;
export const UPDATES_ENABLED = typeof __MAIPA_UPDATES_ENABLED__ !== "undefined" && __MAIPA_UPDATES_ENABLED__;
