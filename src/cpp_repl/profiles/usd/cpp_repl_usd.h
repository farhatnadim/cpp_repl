// cpp_repl_usd.h: the usd profile's prelude, parsed after cpp_repl.h.
//
// The common OpenUSD headers, `using namespace pxr`, and how USD values
// print: a stage or layer as its usda, a prim with its type, authored
// properties and children, an attribute with its value and time samples, and
// a relationship with its targets. Inside a list, a prim or property is its
// path. tree(stage) prints the prim hierarchy.

#pragma once

#include "cpp_repl.h"

#include <filesystem>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

#include <pxr/base/arch/demangle.h>
#include <pxr/base/gf/matrix4d.h>
#include <pxr/base/gf/quatf.h>
#include <pxr/base/gf/vec3d.h>
#include <pxr/base/gf/vec3f.h>
#include <pxr/base/tf/enum.h>
#include <pxr/base/tf/token.h>
#include <pxr/base/vt/array.h>
#include <pxr/base/vt/value.h>

#include <pxr/usd/kind/registry.h>
#include <pxr/usd/sdf/attributeSpec.h>
#include <pxr/usd/sdf/layer.h>
#include <pxr/usd/sdf/path.h>
#include <pxr/usd/sdf/primSpec.h>
#include <pxr/usd/sdf/types.h>

#include <pxr/usd/usd/attribute.h>
#include <pxr/usd/usd/collectionAPI.h>
#include <pxr/usd/usd/editTarget.h>
#include <pxr/usd/usd/inherits.h>
#include <pxr/usd/usd/modelAPI.h>
#include <pxr/usd/usd/payloads.h>
#include <pxr/usd/usd/prim.h>
#include <pxr/usd/usd/primCompositionQuery.h>
#include <pxr/usd/usd/primRange.h>
#include <pxr/usd/usd/references.h>
#include <pxr/usd/usd/relationship.h>
#include <pxr/usd/usd/schemaBase.h>
#include <pxr/usd/usd/specializes.h>
#include <pxr/usd/usd/stage.h>
#include <pxr/usd/usd/variantSets.h>

#include <pxr/usd/usdGeom/basisCurves.h>
#include <pxr/usd/usdGeom/bboxCache.h>
#include <pxr/usd/usdGeom/camera.h>
#include <pxr/usd/usdGeom/capsule.h>
#include <pxr/usd/usdGeom/cone.h>
#include <pxr/usd/usdGeom/cube.h>
#include <pxr/usd/usdGeom/cylinder.h>
#include <pxr/usd/usdGeom/imageable.h>
#include <pxr/usd/usdGeom/mesh.h>
#include <pxr/usd/usdGeom/metrics.h>
#include <pxr/usd/usdGeom/plane.h>
#include <pxr/usd/usdGeom/pointInstancer.h>
#include <pxr/usd/usdGeom/points.h>
#include <pxr/usd/usdGeom/primvar.h>
#include <pxr/usd/usdGeom/primvarsAPI.h>
#include <pxr/usd/usdGeom/scope.h>
#include <pxr/usd/usdGeom/sphere.h>
#include <pxr/usd/usdGeom/tokens.h>
#include <pxr/usd/usdGeom/xform.h>
#include <pxr/usd/usdGeom/xformable.h>
#include <pxr/usd/usdGeom/xformCommonAPI.h>

#include <pxr/usd/usdLux/diskLight.h>
#include <pxr/usd/usdLux/distantLight.h>
#include <pxr/usd/usdLux/domeLight.h>
#include <pxr/usd/usdLux/lightAPI.h>
#include <pxr/usd/usdLux/rectLight.h>
#include <pxr/usd/usdLux/sphereLight.h>

#include <pxr/usd/usdShade/material.h>
#include <pxr/usd/usdShade/materialBindingAPI.h>
#include <pxr/usd/usdShade/shader.h>

using namespace pxr;

namespace repl {
namespace usd {

inline const char *specifier(SdfSpecifier spec)
{
  switch (spec) {
    case SdfSpecifierDef:   return "def";
    case SdfSpecifierOver:  return "over";
    case SdfSpecifierClass: return "class";
    default:                return "?";
  }
}

// A prim, a property, or a schema object: printed as its path inside a list.
template <class T>
inline constexpr bool is_object = std::is_base_of_v<UsdObject, T> || std::is_base_of_v<UsdSchemaBase, T>;

// A wrapper around an attribute (UsdGeomPrimvar, UsdShadeInput, ...).
template <class T>
inline constexpr bool wraps_attribute = !is_object<T> && requires (const T &x) { x.GetAttr().GetPath(); };

inline void show_stage(const UsdStage *stage)
{
  if (!stage) {
    std::cout << "null stage" << std::endl;
    return;
  }
  std::string text;
  stage->ExportToString(&text, /*addSourceFileComment=*/false);
  std::cout << text << std::flush;
}

inline void show_layer(const SdfLayer *layer)
{
  if (!layer) {
    std::cout << "null layer" << std::endl;
    return;
  }
  std::string text;
  layer->ExportToString(&text);
  std::cout << text << std::flush;
}

inline void show_prim(const UsdPrim &prim)
{
  if (!prim) {
    std::cout << "invalid prim" << std::endl;
    return;
  }
  std::cout << specifier(prim.GetSpecifier());
  if (!prim.GetTypeName().IsEmpty())
    std::cout << ' ' << prim.GetTypeName();
  std::cout << ' ' << prim.GetPath() << "\n  authored properties:";
  const TfTokenVector names = prim.GetAuthoredPropertyNames();
  if (names.empty())
    std::cout << " none";
  for (const TfToken &name : names)
    std::cout << ' ' << name;
  std::cout << "\n  children:";
  bool any = false;
  for (const UsdPrim &child : prim.GetAllChildren()) {
    std::cout << ' ' << child.GetName();
    any = true;
  }
  if (!any)
    std::cout << " none";
  std::cout << std::endl;
}

inline void show_attribute(const UsdAttribute &attr)
{
  if (!attr) {
    std::cout << "invalid attribute" << std::endl;
    return;
  }
  std::cout << attr.GetPath() << " (" << attr.GetTypeName().GetAsToken() << ')';
  VtValue value;
  if (attr.Get(&value))
    std::cout << " = " << value;
  else
    std::cout << " has no default value";
  std::vector<double> times;
  if (attr.GetTimeSamples(&times) && !times.empty()) {
    std::cout << "\n  time samples: {";
    for (std::size_t i = 0; i < times.size(); ++i) {
      VtValue sample;
      attr.Get(&sample, times[i]);
      std::cout << (i ? ", " : "") << times[i] << ": " << sample;
    }
    std::cout << '}';
  }
  std::cout << std::endl;
}

inline void show_relationship(const UsdRelationship &rel)
{
  if (!rel) {
    std::cout << "invalid relationship" << std::endl;
    return;
  }
  SdfPathVector targets;
  rel.GetTargets(&targets);
  std::cout << rel.GetPath() << " ->";
  if (targets.empty())
    std::cout << " no targets";
  for (const SdfPath &target : targets)
    std::cout << ' ' << target;
  std::cout << std::endl;
}

}  // namespace usd

template <class T>
  requires usd::is_object<T>
struct Put<T> {
  static void put(std::ostream &os, const T &x) { os << x.GetPath(); }
};

template <class T>
  requires usd::wraps_attribute<T>
struct Put<T> {
  static void put(std::ostream &os, const T &x) { os << x.GetAttr().GetPath(); }
};

// An enum registered with TfEnum prints its name.
template <class T>
  requires std::is_enum_v<T>
struct Put<T> {
  static void put(std::ostream &os, const T &x)
  {
    const std::string name = TfEnum::GetName(TfEnum(x));
    if (name.empty())
      os << type_name<T>() << '(' << +static_cast<std::underlying_type_t<T>>(x) << ')';
    else
      os << name;
  }
};

template <>
struct Show<UsdStageRefPtr> {
  static void show(const UsdStageRefPtr &stage) { usd::show_stage(get_pointer(stage)); }
};
template <>
struct Show<UsdStagePtr> {
  static void show(const UsdStagePtr &stage) { usd::show_stage(get_pointer(stage)); }
};
template <>
struct Show<SdfLayerRefPtr> {
  static void show(const SdfLayerRefPtr &layer) { usd::show_layer(get_pointer(layer)); }
};
template <>
struct Show<SdfLayerHandle> {
  static void show(const SdfLayerHandle &layer) { usd::show_layer(get_pointer(layer)); }
};
template <>
struct Show<UsdPrim> {
  static void show(const UsdPrim &prim) { usd::show_prim(prim); }
};
template <>
struct Show<UsdAttribute> {
  static void show(const UsdAttribute &attr) { usd::show_attribute(attr); }
};
template <>
struct Show<UsdRelationship> {
  static void show(const UsdRelationship &rel) { usd::show_relationship(rel); }
};
template <>
struct Show<UsdProperty> {
  static void show(const UsdProperty &prop)
  {
    if (prop.Is<UsdAttribute>())
      usd::show_attribute(prop.As<UsdAttribute>());
    else if (prop.Is<UsdRelationship>())
      usd::show_relationship(prop.As<UsdRelationship>());
    else
      std::cout << "invalid property" << std::endl;
  }
};

// A schema object shows its class and its prim.
template <class T>
  requires std::is_base_of_v<UsdSchemaBase, T>
struct Show<T> {
  static void show(const T &x)
  {
    std::cout << ArchGetDemangled<T>() << ": ";
    usd::show_prim(x.GetPrim());
  }
};

// A wrapper around an attribute shows the attribute, unless it prints itself.
template <class T>
  requires (usd::wraps_attribute<T> && !requires (std::ostream &out, const T &x) { out << x; })
struct Show<T> {
  static void show(const T &x) { usd::show_attribute(x.GetAttr()); }
};

// A spec handle (SdfPrimSpecHandle, SdfAttributeSpecHandle, ...) shows its
// layer and path: @tmp.usda@</World>, on its own and inside a list alike.
template <class S>
struct Put<SdfHandle<S>> {
  static void put(std::ostream &os, const SdfHandle<S> &spec)
  {
    if (!spec)
      os << "expired spec";
    else
      os << '@' << spec->GetLayer()->GetDisplayName() << "@<" << spec->GetPath() << '>';
  }
};
template <class S>
struct Show<SdfHandle<S>> {
  static void show(const SdfHandle<S> &spec)
  {
    Put<SdfHandle<S>>::put(std::cout, spec);
    std::cout << std::endl;
  }
};

// The prim hierarchy, one line per prim: specifier, type, name.
inline void tree(const UsdPrim &prim, int depth = 0)
{
  std::cout << std::string(2 * depth, ' ') << usd::specifier(prim.GetSpecifier());
  if (!prim.GetTypeName().IsEmpty())
    std::cout << ' ' << prim.GetTypeName();
  std::cout << " \"" << prim.GetName() << '"' << std::endl;
  for (const UsdPrim &child : prim.GetAllChildren())
    tree(child, depth + 1);
}

inline void tree(const UsdStageRefPtr &stage)
{
  if (!stage) {
    std::cout << "null stage" << std::endl;
    return;
  }
  for (const UsdPrim &prim : stage->GetPseudoRoot().GetAllChildren())
    tree(prim);
}

}  // namespace repl

using repl::tree;
