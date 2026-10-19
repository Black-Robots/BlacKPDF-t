/* BlackPDF - capa de acceso a nanoPRC.

    Copyright (C) 2026 BlackPDF

    This program is free software: you can redistribute it and/or modify it
    under the terms of the GNU Affero General Public License as published by
    the Free Software Foundation, either version 3 of the License, or (at
    your option) any later version.

    Uses nanoPRC (Copyright (C) 2023-2026 CascadiaVoxel LLC, AGPL-3.0) to
    read PRC files, including highly compressed tessellation and exact
    (B-rep) geometry, and returns the meshes in a flat binary block that
    Python reads with the struct module. Wire and feature edges are computed
    here because Python would be too slow for large CAD assemblies.

    Block layout (little endian):
        "BPRC" u32 version  u32 part_count
        per part:
            u32 name_length  name (UTF-8)
            f64 matrix[16]   world transform, column major
            u32 visible
            u32 batch_count
            per batch:
                u32 kind      0 triangles, 1 lines
                u32 flags     bit 0: per-vertex colors
                f32 color[4]
                u32 vertex_count
                f32 positions[3n]
                f32 normals[3n]                  (triangles)
                f32 colors[4n]                   (flag bit 0)
                u32 wire_vertices  f32 wire[3w]  (triangles)
                u32 edge_vertices  f32 edges[3e] (triangles)
*/

#include <prc_api.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define BPRC_EXPORT __declspec(dllexport)
#else
#define BPRC_EXPORT __attribute__((visibility("default")))
#endif

#define BPRC_VERSION 1
#define BATCH_TRIANGLES 0
#define BATCH_LINES 1
#define FLAG_VERTEX_COLORS 1
/* cos(35 grados): entre caras más dobladas que esto la arista es viva. */
#define FEATURE_COS 0.81915204f

/* -- memoria ------------------------------------------------------------ */

typedef struct
{
    unsigned char *data;
    size_t size;
    size_t capacity;
    int failed;
} buffer;

static void put(buffer *b, const void *src, size_t size)
{
    if (b->failed || size == 0)
        return;
    if (b->size + size > b->capacity)
    {
        size_t capacity = b->capacity ? b->capacity : 1 << 16;
        while (capacity < b->size + size)
            capacity *= 2;
        unsigned char *grown = (unsigned char *)realloc(b->data, capacity);
        if (grown == NULL)
        {
            b->failed = 1;
            return;
        }
        b->data = grown;
        b->capacity = capacity;
    }
    memcpy(b->data + b->size, src, size);
    b->size += size;
}

static void put_u32(buffer *b, uint32_t value)
{
    put(b, &value, 4);
}

typedef struct
{
    float *values;
    size_t count;
    size_t capacity;
    int failed;
} floats;

static void floats_push(floats *f, const float *src, size_t count)
{
    if (f->failed)
        return;
    if (f->count + count > f->capacity)
    {
        size_t capacity = f->capacity ? f->capacity : 1024;
        while (capacity < f->count + count)
            capacity *= 2;
        float *grown = (float *)realloc(f->values, capacity * sizeof(float));
        if (grown == NULL)
        {
            f->failed = 1;
            return;
        }
        f->values = grown;
        f->capacity = capacity;
    }
    memcpy(f->values + f->count, src, count * sizeof(float));
    f->count += count;
}

static void floats_free(floats *f)
{
    free(f->values);
    memset(f, 0, sizeof(*f));
}

/* -- lotes por color ------------------------------------------------------ */

typedef struct
{
    uint32_t kind;
    uint32_t flags;
    float color[4];
    floats positions;
    floats normals;
    floats colors;
} batch;

typedef struct
{
    batch *items;
    size_t count;
    size_t capacity;
    int failed;
} batches;

static const float default_color[4] = {0.72f, 0.72f, 0.74f, 1.0f};

static batch *batch_for(batches *list, uint32_t kind, uint32_t flags, const float color[4])
{
    for (size_t k = 0; k < list->count; k++)
    {
        batch *item = list->items + k;
        if (item->kind == kind && item->flags == flags && memcmp(item->color, color, sizeof(item->color)) == 0)
            return item;
    }
    if (list->failed)
        return NULL;
    if (list->count == list->capacity)
    {
        size_t capacity = list->capacity ? list->capacity * 2 : 8;
        batch *grown = (batch *)realloc(list->items, capacity * sizeof(batch));
        if (grown == NULL)
        {
            list->failed = 1;
            return NULL;
        }
        list->items = grown;
        list->capacity = capacity;
    }
    batch *item = list->items + list->count++;
    memset(item, 0, sizeof(*item));
    item->kind = kind;
    item->flags = flags;
    memcpy(item->color, color, sizeof(item->color));
    return item;
}

static int batch_failed(const batch *item)
{
    return item->positions.failed || item->normals.failed || item->colors.failed;
}

static void batches_free(batches *list)
{
    for (size_t k = 0; k < list->count; k++)
    {
        floats_free(&list->items[k].positions);
        floats_free(&list->items[k].normals);
        floats_free(&list->items[k].colors);
    }
    free(list->items);
    memset(list, 0, sizeof(*list));
}

/* -- geometría ------------------------------------------------------------ */

static int normalize3(float v[3])
{
    float length = sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    if (!(length > 1e-30f) || !isfinite(length))
        return 0;
    v[0] /= length;
    v[1] /= length;
    v[2] /= length;
    return 1;
}

static int triangle_normal(const float *a, const float *b, const float *c, float out[3])
{
    float u[3] = {b[0] - a[0], b[1] - a[1], b[2] - a[2]};
    float w[3] = {c[0] - a[0], c[1] - a[1], c[2] - a[2]};
    out[0] = u[1] * w[2] - u[2] * w[1];
    out[1] = u[2] * w[0] - u[0] * w[2];
    out[2] = u[0] * w[1] - u[1] * w[0];
    return normalize3(out);
}

static void clamp_color(float color[4])
{
    for (int k = 0; k < 4; k++)
    {
        if (!isfinite(color[k]) || color[k] < 0.0f)
            color[k] = 0.0f;
        else if (color[k] > 1.0f)
            color[k] = 1.0f;
    }
    if (color[3] <= 0.0f)
        color[3] = 1.0f;
}

/* Contexto de una cara: dónde van sus triángulos y de qué color es. */
typedef struct
{
    batches *list;
    size_t last;          /* índice + 1 del último lote usado (suele repetirse) */
    float face_color[4];  /* material de la cara o color por defecto */
    int face_material;
    int vertex_style;     /* estilos por triángulo (teselación comprimida) */
} painter;

#define MAX_UNIFORM_BATCHES 256

static batch *painter_batch(painter *p, uint32_t kind, uint32_t flags, const float color[4])
{
    if (p->last > 0 && p->last <= p->list->count)
    {
        batch *item = p->list->items + p->last - 1;
        if (item->kind == kind && item->flags == flags && memcmp(item->color, color, sizeof(item->color)) == 0)
            return item;
    }
    batch *item = batch_for(p->list, kind, flags, color);
    if (item != NULL)
        p->last = (size_t)(item - p->list->items) + 1;
    return item;
}

static int color_set(const float c[4])
{
    return c[0] != 0.0f || c[1] != 0.0f || c[2] != 0.0f || c[3] != 0.0f;
}

/* Color de una esquina. nanoPRC lo deja en sitios distintos según el caso:
   estilo por triángulo en `diffuse`, color del estilo RGB o color por
   vértice en `color` (blanco cuando manda el material de la cara) y el
   material de la cara aparte. */
static void corner_color(const painter *p, const prc_api_vertex *v, float out[4])
{
    if (p->vertex_style && v->tri_has_material)
    {
        out[0] = v->diffuse[0];
        out[1] = v->diffuse[1];
        out[2] = v->diffuse[2];
        out[3] = v->alpha;
    }
    else if (color_set(v->color) &&
             !(p->face_material && v->color[0] == 1.0f && v->color[1] == 1.0f && v->color[2] == 1.0f))
    {
        memcpy(out, v->color, sizeof(float) * 4);
    }
    else
    {
        memcpy(out, p->face_color, sizeof(float) * 4);
    }
    clamp_color(out);
}

static void add_triangle(painter *p, const prc_api_vertex *vertices, size_t count, uint32_t i0, uint32_t i1,
    uint32_t i2)
{
    if (i0 >= count || i1 >= count || i2 >= count)
        return;
    const prc_api_vertex *corner[3] = {vertices + i0, vertices + i1, vertices + i2};
    float colors[3][4];
    for (int k = 0; k < 3; k++)
        corner_color(p, corner[k], colors[k]);
    batch *item;
    int uniform = memcmp(colors[0], colors[1], sizeof(colors[0])) == 0 &&
                  memcmp(colors[0], colors[2], sizeof(colors[0])) == 0;
    if (uniform && (p->list->count < MAX_UNIFORM_BATCHES ||
                    (p->last > 0 && memcmp(p->list->items[p->last - 1].color, colors[0], sizeof(colors[0])) == 0)))
        item = painter_batch(p, BATCH_TRIANGLES, 0, colors[0]);
    else
        item = painter_batch(p, BATCH_TRIANGLES, FLAG_VERTEX_COLORS, p->face_color);
    if (item == NULL)
        return;
    float flat[3];
    if (!triangle_normal(corner[0]->position, corner[1]->position, corner[2]->position, flat))
    {
        flat[0] = 0.0f;
        flat[1] = 0.0f;
        flat[2] = 1.0f;
    }
    for (int k = 0; k < 3; k++)
    {
        float normal[3] = {flat[0], flat[1], flat[2]};
        if (corner[k]->normal_set)
        {
            memcpy(normal, corner[k]->normal, sizeof(normal));
            if (!normalize3(normal))
                memcpy(normal, flat, sizeof(normal));
        }
        floats_push(&item->positions, corner[k]->position, 3);
        floats_push(&item->normals, normal, 3);
        if (item->flags & FLAG_VERTEX_COLORS)
            floats_push(&item->colors, colors[k], 4);
    }
}

static void add_segment(painter *p, const prc_api_vertex *vertices, size_t count, uint32_t i0, uint32_t i1)
{
    if (i0 >= count || i1 >= count)
        return;
    float color[4];
    corner_color(p, vertices + i0, color);
    batch *item = painter_batch(p, BATCH_LINES, 0, color);
    if (item == NULL)
        return;
    floats_push(&item->positions, vertices[i0].position, 3);
    floats_push(&item->positions, vertices[i1].position, 3);
}

static void add_primitive(painter *p, const prc_api_graphic_primitive *prim, const prc_api_vertex *vertices,
    size_t count)
{
    size_t n = prim->num_indices;
    const uint32_t *idx = prim->indices;
    size_t k;
    if (idx == NULL || n == 0)
        return;
    switch (prim->type)
    {
    case PRC_API_TRIANGLES:
        for (k = 0; k + 2 < n; k += 3)
            add_triangle(p, vertices, count, idx[k], idx[k + 1], idx[k + 2]);
        break;
    case PRC_API_STRIP:
        for (k = 2; k < n; k++)
        {
            if (k % 2 == 0)
                add_triangle(p, vertices, count, idx[k - 2], idx[k - 1], idx[k]);
            else
                add_triangle(p, vertices, count, idx[k - 1], idx[k - 2], idx[k]);
        }
        break;
    case PRC_API_FAN:
        for (k = 2; k < n; k++)
            add_triangle(p, vertices, count, idx[0], idx[k - 1], idx[k]);
        break;
    case PRC_API_LINE:
        for (k = 0; k + 1 < n; k += 2)
            add_segment(p, vertices, count, idx[k], idx[k + 1]);
        break;
    case PRC_API_LINE_STRIP:
    case PRC_API_LINE_LOOP:
        for (k = 1; k < n; k++)
            add_segment(p, vertices, count, idx[k - 1], idx[k]);
        if (prim->type == PRC_API_LINE_LOOP && n > 2)
            add_segment(p, vertices, count, idx[n - 1], idx[0]);
        break;
    default:
        break;
    }
}

static void collect_tessellation(prc_context *ctx, prc_api_data data, prc_api_tess *tess, batches *list)
{
    size_t face_max;
    if (tess == NULL || tess->type == PRC_API_TESS_UNKNOWN || tess->type == PRC_API_TESS_MarkUp)
        return;
    if (tess->type == PRC_API_TESS_3D_Wire)
        face_max = 1;
    else if (tess->type == PRC_API_EXACT_GEOM)
        face_max = prc_api_get_num_graphics_primitives(ctx, data, tess);
    else
        face_max = tess->tess_faces != NULL ? tess->num_faces : 0;

    painter p;
    memset(&p, 0, sizeof(p));
    p.list = list;
    for (uint32_t i = 0; i < face_max; i++)
    {
        if (prc_api_skip_face(ctx, tess, i))
            continue;
        memcpy(p.face_color, default_color, sizeof(p.face_color));
        p.face_material = prc_api_face_is_material(ctx, tess, i) > 0;
        if (p.face_material)
        {
            prc_api_material material;
            memset(&material, 0, sizeof(material));
            prc_api_get_face_material(ctx, tess, &material, i);
            p.face_color[0] = material.diffuse[0];
            p.face_color[1] = material.diffuse[1];
            p.face_color[2] = material.diffuse[2];
            p.face_color[3] = material.diffuse_alpha;
        }
        else if (tess->is_material)
        {
            p.face_material = 1;
            p.face_color[0] = tess->tess_material.diffuse[0];
            p.face_color[1] = tess->tess_material.diffuse[1];
            p.face_color[2] = tess->tess_material.diffuse[2];
            p.face_color[3] = tess->tess_material.diffuse_alpha;
        }
        clamp_color(p.face_color);
        p.vertex_style = prc_api_vertices_have_material(ctx, tess, i) > 0;

        size_t primitives;
        if (tess->type == PRC_API_TESS_3D_Wire)
            primitives = tess->num_line_primitives;
        else if (tess->type == PRC_API_EXACT_GEOM)
            primitives = 1;
        else
            primitives = tess->tess_faces[i].num_graphic_primitives;

        prc_api_vertex *vertices = NULL;
        uint32_t vertex_count = 0;
        if (prc_api_get_face_vertices(ctx, tess, i, &vertex_count, &vertices) < 0 || vertices == NULL)
            continue;
        for (size_t j = 0; j < primitives; j++)
        {
            prc_api_graphic_primitive prim;
            memset(&prim, 0, sizeof(prim));
            if (prc_api_get_graphics_primitive(ctx, data, tess, i, j, &prim) < 0)
                continue;
            add_primitive(&p, &prim, vertices, vertex_count);
        }
    }
}

/* -- aristas -------------------------------------------------------------- */

static uint32_t hash_u32(uint32_t x)
{
    x ^= x >> 16;
    x *= 0x7feb352dU;
    x ^= x >> 15;
    x *= 0x846ca68bU;
    x ^= x >> 16;
    return x;
}

static uint32_t position_hash(const float *p)
{
    uint32_t bits[3];
    float clean[3] = {p[0] + 0.0f, p[1] + 0.0f, p[2] + 0.0f}; /* -0 y 0 son el mismo punto */
    memcpy(bits, clean, sizeof(bits));
    return hash_u32(bits[0] ^ hash_u32(bits[1] ^ hash_u32(bits[2])));
}

static size_t table_size(size_t items)
{
    size_t size = 16;
    while (size < items * 2)
        size *= 2;
    return size;
}

/* Une los vértices con la misma posición: ids[v] es el primer vértice igual. */
static uint32_t *weld(const float *positions, size_t count)
{
    size_t size = table_size(count);
    uint32_t *table = (uint32_t *)malloc(size * sizeof(uint32_t));
    uint32_t *ids = (uint32_t *)malloc((count ? count : 1) * sizeof(uint32_t));
    if (table == NULL || ids == NULL)
    {
        free(table);
        free(ids);
        return NULL;
    }
    memset(table, 0xFF, size * sizeof(uint32_t));
    for (size_t v = 0; v < count; v++)
    {
        const float *p = positions + v * 3;
        size_t slot = position_hash(p) & (size - 1);
        for (;;)
        {
            uint32_t other = table[slot];
            if (other == UINT32_MAX)
            {
                table[slot] = (uint32_t)v;
                ids[v] = (uint32_t)v;
                break;
            }
            const float *q = positions + (size_t)other * 3;
            if (p[0] == q[0] && p[1] == q[1] && p[2] == q[2])
            {
                ids[v] = other;
                break;
            }
            slot = (slot + 1) & (size - 1);
        }
    }
    free(table);
    return ids;
}

typedef struct
{
    uint32_t a, b; /* vértices soldados, a < b */
    uint32_t face0;
    uint32_t face1;
    uint32_t count;
} edge;

static void compute_edges(const batch *item, floats *wire, floats *edges)
{
    const float *positions = item->positions.values;
    size_t vertex_count = item->positions.count / 3;
    size_t triangles = vertex_count / 3;
    uint32_t *ids = NULL;
    float *normals = NULL;
    unsigned char *valid = NULL;
    edge *table = NULL;
    size_t size;
    if (triangles == 0)
        return;
    ids = weld(positions, vertex_count);
    normals = (float *)malloc(triangles * 3 * sizeof(float));
    valid = (unsigned char *)malloc(triangles);
    size = table_size(triangles * 3);
    table = (edge *)calloc(size, sizeof(edge));
    if (ids == NULL || normals == NULL || valid == NULL || table == NULL)
    {
        wire->failed = 1;
        goto cleanup;
    }
    for (size_t t = 0; t < triangles; t++)
    {
        const float *p = positions + t * 9;
        valid[t] = (unsigned char)triangle_normal(p, p + 3, p + 6, normals + t * 3);
    }
    for (size_t t = 0; t < triangles; t++)
    {
        for (int k = 0; k < 3; k++)
        {
            uint32_t p = ids[t * 3 + k];
            uint32_t q = ids[t * 3 + (k + 1) % 3];
            if (p == q)
                continue;
            uint32_t a = p < q ? p : q;
            uint32_t b = p < q ? q : p;
            size_t slot = hash_u32(a * 0x9E3779B1U ^ hash_u32(b)) & (size - 1);
            for (;;)
            {
                edge *e = table + slot;
                if (e->count == 0)
                {
                    e->a = a;
                    e->b = b;
                    e->face0 = (uint32_t)t;
                    e->count = 1;
                    break;
                }
                if (e->a == a && e->b == b)
                {
                    if (e->count == 1)
                        e->face1 = (uint32_t)t;
                    e->count++;
                    break;
                }
                slot = (slot + 1) & (size - 1);
            }
        }
    }
    for (size_t slot = 0; slot < size; slot++)
    {
        const edge *e = table + slot;
        int feature;
        if (e->count == 0)
            continue;
        floats_push(wire, positions + (size_t)e->a * 3, 3);
        floats_push(wire, positions + (size_t)e->b * 3, 3);
        if (e->count != 2)
        {
            feature = 1;
        }
        else if (!valid[e->face0] || !valid[e->face1])
        {
            feature = 0;
        }
        else
        {
            const float *n0 = normals + (size_t)e->face0 * 3;
            const float *n1 = normals + (size_t)e->face1 * 3;
            /* En valor absoluto: las teselaciones comprimidas traen
               triángulos vecinos con el giro invertido. */
            feature = fabsf(n0[0] * n1[0] + n0[1] * n1[1] + n0[2] * n1[2]) < FEATURE_COS;
        }
        if (feature)
        {
            floats_push(edges, positions + (size_t)e->a * 3, 3);
            floats_push(edges, positions + (size_t)e->b * 3, 3);
        }
    }
cleanup:
    free(ids);
    free(normals);
    free(valid);
    free(table);
}

/* -- salida --------------------------------------------------------------- */

static void write_batch(buffer *out, const batch *item)
{
    size_t n = item->positions.count / 3;
    floats wire, edges;
    put_u32(out, item->kind);
    put_u32(out, item->flags);
    put(out, item->color, sizeof(item->color));
    put_u32(out, (uint32_t)n);
    put(out, item->positions.values, n * 3 * sizeof(float));
    if (item->kind != BATCH_TRIANGLES)
        return;
    put(out, item->normals.values, n * 3 * sizeof(float));
    if (item->flags & FLAG_VERTEX_COLORS)
        put(out, item->colors.values, n * 4 * sizeof(float));
    memset(&wire, 0, sizeof(wire));
    memset(&edges, 0, sizeof(edges));
    compute_edges(item, &wire, &edges);
    if (wire.failed || edges.failed)
        out->failed = 1;
    put_u32(out, (uint32_t)(wire.count / 3));
    put(out, wire.values, wire.count * sizeof(float));
    put_u32(out, (uint32_t)(edges.count / 3));
    put(out, edges.values, edges.count * sizeof(float));
    floats_free(&wire);
    floats_free(&edges);
}

static void write_part(buffer *out, uint32_t *parts, const char *name, const prc_api_transform *world, int visible,
    batches *list)
{
    uint32_t used = 0;
    double matrix[16];
    for (size_t k = 0; k < list->count; k++)
    {
        if (batch_failed(list->items + k))
        {
            out->failed = 1;
            return;
        }
        if (list->items[k].positions.count > 0)
            used++;
    }
    if (list->failed)
        out->failed = 1;
    if (used == 0 || out->failed)
        return;
    uint32_t length = name ? (uint32_t)strlen(name) : 0;
    put_u32(out, length);
    put(out, name, length);
    if (world == NULL || world->is_identity)
    {
        memset(matrix, 0, sizeof(matrix));
        matrix[0] = matrix[5] = matrix[10] = matrix[15] = 1.0;
    }
    else
    {
        memcpy(matrix, world->matrix, sizeof(matrix));
    }
    put(out, matrix, sizeof(matrix));
    put_u32(out, visible ? 1 : 0);
    put_u32(out, used);
    for (size_t k = 0; k < list->count; k++)
    {
        if (list->items[k].positions.count > 0)
            write_batch(out, list->items + k);
    }
    (*parts)++;
}

typedef struct
{
    prc_context *ctx;
    prc_api_data data;
    buffer *out;
    uint32_t parts;
} walker;

static void emit(walker *w, const char *name, const prc_api_transform *world, int visible, prc_api_tess *tess)
{
    batches list;
    memset(&list, 0, sizeof(list));
    collect_tessellation(w->ctx, w->data, tess, &list);
    write_part(w->out, &w->parts, name, world, visible, &list);
    batches_free(&list);
}

/* nanoPRC pone "node" a los elementos sin nombre. */
static int has_name(const char *text)
{
    return text != NULL && text[0] != '\0' && strcmp(text, "node") != 0;
}

/* La visibilidad no se toma de nanoPRC: no resuelve los gráficos heredados
   ("igual que el actual") y marca como ocultas piezas visibles. */
static void walk_rep_items(walker *w, prc_api_part *part, const char *owner, const prc_api_transform *world,
    int depth)
{
    if (part == NULL || depth > 64)
        return;
    for (uint32_t k = 0; k < part->num_rep_items; k++)
    {
        prc_api_part *item = part->rep_items + k;
        const char *name = has_name(item->name) && !item->name_same_as_product ? item->name : owner;
        prc_api_tess *tess = prc_api_get_ri_tessellation(w->ctx, part, k);
        if (tess != NULL)
            emit(w, name, world, 1, tess);
        if (item->num_rep_items > 0)
            walk_rep_items(w, item, name, world, depth + 1);
    }
}

/* ``owner`` es el nombre heredado: un producto que es el único hijo de otro
   (p. ej. la configuración "Default" de una pieza) toma el nombre del padre. */
static void walk_product(walker *w, prc_api_product *node, prc_api_transform parent, const char *owner, int depth)
{
    if (node == NULL || depth > 128)
        return;
    prc_api_transform world = parent;
    if (node->location_set)
        prc_api_update_transform(w->ctx, &world, &node->location);
    const char *name = has_name(owner) ? owner : has_name(node->name) ? node->name : "";
    if (node->part != NULL)
    {
        if (!has_name(name) && has_name(node->part->name))
            name = node->part->name;
        if (node->part->tess != NULL)
            emit(w, name, &world, 1, node->part->tess);
        walk_rep_items(w, node->part, name, &world, 0);
    }
    for (size_t k = 0; k < node->num_children; k++)
    {
        const char *inherited = node->num_children == 1 && !node->is_model ? name : NULL;
        walk_product(w, node->children + k, world, inherited, depth + 1);
    }
}

/* -- carga ---------------------------------------------------------------- */

typedef struct
{
    prc_api_tess *tesses;
    uint32_t tess_count;
    prc_api_tess *exact;
    uint32_t exact_count;
} storage;

static void storage_free(storage *s)
{
    for (uint32_t k = 0; k < s->tess_count; k++)
        free(s->tesses[k].tess_faces);
    for (uint32_t k = 0; k < s->exact_count; k++)
    {
        if (s->exact[k].shells == NULL)
            continue;
        for (size_t j = 0; j < s->exact[k].num_shells; j++)
            free(s->exact[k].shells[j].shell_faces);
        free(s->exact[k].shells);
    }
    free(s->tesses);
    free(s->exact);
    memset(s, 0, sizeof(*s));
}

/* Las líneas de pliegue que nanoPRC añade a las teselaciones comprimidas no
   se piden: las aristas vivas se calculan aquí para todas las mallas. */
static int load_tessellations(prc_context *ctx, prc_api_data data, prc_api_product *tree, storage *s)
{
    for (uint32_t k = 0; k < s->tess_count; k++)
    {
        prc_api_tess *tess = &s->tesses[k];
        uint8_t has_line = 0;
        uint32_t faces = prc_api_get_number_faces(ctx, data, k);
        if (prc_api_get_tessellation_type(ctx, data, k) == PRC_API_TESS_3D_Compressed)
            faces = 1;
        tess->num_faces = faces;
        tess->tess_faces = (prc_api_face *)calloc(faces ? faces : 1, sizeof(prc_api_face));
        if (tess->tess_faces == NULL)
            return -1;
        if (prc_api_initialize_tessellation(ctx, data, tree, k, tess, NULL, &has_line) < 0)
        {
            tess->type = PRC_API_TESS_UNKNOWN;
            continue;
        }
        if (tess->type == PRC_API_TESS_3D_Wire || tess->type == PRC_API_TESS_MarkUp)
        {
            prc_api_get_tessellation_vertices(ctx, data, k, 0, NULL, tess);
        }
        else if (tess->type != PRC_API_TESS_UNKNOWN)
        {
            for (uint32_t j = 0; j < tess->num_faces; j++)
                prc_api_get_tessellation_vertices(ctx, data, k, j, tess->tess_faces + j, tess);
        }
    }
    return 0;
}

static int load_exact_geometry(prc_context *ctx, prc_api_data data, prc_api_product *tree, storage *s)
{
    uint32_t objects = prc_api_get_number_exact_geom_objects(ctx, data);
    uint32_t counter = 0;
    if (objects == 0)
        return 0;
    s->exact = (prc_api_tess *)calloc(objects, sizeof(prc_api_tess));
    if (s->exact == NULL)
        return -1;
    s->exact_count = objects;
    for (uint32_t k = 0; k < objects; k++)
    {
        uint32_t shells = prc_api_get_number_exact_geom_shells(ctx, data, k);
        s->exact[k].num_shells = shells;
        s->exact[k].shells = (prc_api_shell *)calloc(shells ? shells : 1, sizeof(prc_api_shell));
        if (s->exact[k].shells == NULL)
            return -1;
        for (uint32_t j = 0; j < shells; j++)
        {
            uint32_t faces = prc_api_get_number_exact_geom_faces(ctx, data, k, j);
            s->exact[k].shells[j].num_faces = faces;
            s->exact[k].shells[j].shell_faces = (prc_api_face *)calloc(faces ? faces : 1, sizeof(prc_api_face));
            if (s->exact[k].shells[j].shell_faces == NULL)
                return -1;
            for (uint32_t i = 0; i < faces; i++)
                prc_api_get_exact_geometry_tessellation_vertices(ctx, data, tree, k, j, i, counter++, s->exact);
        }
    }
    return 0;
}

/* Lee el PRC (o PDF) de ``path``. Devuelve 0 y un bloque en *out que hay que
   liberar con bprc_free; -1 sin memoria, -2 argumentos, -3 archivo ilegible. */
BPRC_EXPORT int bprc_load(const char *path, unsigned char **out, size_t *out_size)
{
    prc_context *ctx;
    prc_api_data data;
    prc_api_product *tree = NULL;
    uint32_t num_parts = 0, num_products = 0, num_markups = 0;
    uint32_t total = 0, total_lines = 0, total_exact = 0;
    storage s;
    buffer result;
    walker w;
    int code = 0;

    if (out == NULL || out_size == NULL || path == NULL)
        return -2;
    *out = NULL;
    *out_size = 0;
    memset(&s, 0, sizeof(s));
    memset(&result, 0, sizeof(result));

    ctx = prc_api_new_context(NULL);
    if (ctx == NULL)
        return -1;
    data = prc_api_open_contents(ctx, path);
    if (data == NULL)
    {
        prc_api_release_context(ctx);
        return -3;
    }
    if (prc_api_prep_model_tree(ctx, data, &num_parts, &num_products, &num_markups) < 0 ||
        prc_api_create_model_tree(ctx, data, &tree, num_parts, num_products, num_markups) < 0 ||
        tree == NULL ||
        prc_api_get_number_tessellations(ctx, data, tree, &total, &total_lines, &total_exact, 0) < 0)
    {
        code = -3;
        goto done;
    }

    if (total > 0)
    {
        s.tesses = (prc_api_tess *)calloc(total, sizeof(prc_api_tess));
        if (s.tesses == NULL)
        {
            code = -1;
            goto done;
        }
        s.tess_count = total;
    }
    if (load_tessellations(ctx, data, tree, &s) < 0 ||
        (total_exact > 0 && load_exact_geometry(ctx, data, tree, &s) < 0))
    {
        code = -1;
        goto done;
    }

    put(&result, "BPRC", 4);
    put_u32(&result, BPRC_VERSION);
    put_u32(&result, 0); /* número de piezas: se rellena al final */
    w.ctx = ctx;
    w.data = data;
    w.out = &result;
    w.parts = 0;
    {
        prc_api_transform identity;
        prc_api_set_transform_identity(ctx, &identity);
        walk_product(&w, tree, identity, "", 0);
    }
    if (result.failed)
    {
        code = -1;
        goto done;
    }
    memcpy(result.data + 8, &w.parts, 4);
    *out = result.data;
    *out_size = result.size;
    result.data = NULL;

done:
    free(result.data);
    /* nanoPRC libera lo que reservó él; los arrays de caras y cáscaras son nuestros. */
    prc_api_release_data(ctx, data, s.tesses, s.tess_count, NULL, 0, s.exact, s.exact_count, tree);
    storage_free(&s);
    prc_api_release_context(ctx);
    return code;
}

BPRC_EXPORT void bprc_free(unsigned char *block)
{
    free(block);
}

BPRC_EXPORT int bprc_version(void)
{
    return BPRC_VERSION;
}
