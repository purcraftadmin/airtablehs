<?php
/**
 * Plugin Name: WC Inventory Webhook Transform
 * Description: Transforms WooCommerce order webhooks for the inventory sync service.
 * Version:     1.0.0
 *
 * INSTALL: Copy this file to wp-content/mu-plugins/ on each WC site.
 *
 * CONFIGURE in wp-config.php:
 *   define( 'WC_INV_SITE_ID', 'shop1' );   // unique id matching SITES config
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

add_filter(
    'woocommerce_webhook_payload',
    'wc_inv_transform_order_payload',
    10,
    4
);

/**
 * @param array  $payload     Original WC webhook payload.
 * @param string $resource    Resource type ('order', 'product', etc.).
 * @param int    $resource_id WC object ID.
 * @param int    $webhook_id  Webhook post ID.
 *
 * @return array  Modified payload for order resources, original otherwise.
 */
function wc_inv_transform_order_payload( $payload, $resource, $resource_id, $webhook_id ) {
    if ( 'order' !== $resource ) {
        return $payload;
    }

    $order = wc_get_order( $resource_id );
    if ( ! $order ) {
        return $payload;
    }

    $site_id = defined( 'WC_INV_SITE_ID' ) ? WC_INV_SITE_ID : sanitize_title( get_bloginfo( 'name' ) );

    $line_items = [];
    foreach ( $order->get_items() as $item_id => $item ) {
        /** @var WC_Order_Item_Product $item */
        $product = $item->get_product();
        if ( ! $product ) {
            continue;
        }

        // Prefer variation SKU, fall back to parent SKU
        $sku = $product->get_sku();
        if ( ! $sku ) {
            continue;
        }

        $line_items[] = [
            'line_item_id' => (string) $item_id,
            'sku'          => $sku,
            'qty'          => (int) $item->get_quantity(),
        ];
    }

    return [
        'site_id'    => $site_id,
        'order_id'   => (string) $resource_id,
        'status'     => $order->get_status(),
        'line_items' => $line_items,
    ];
}

/**
 * Transform refund/cancellation payload.
 *
 * For refund_or_cancel webhook, include the event_type field.
 * Hook into a custom filter if you send refunds to a separate endpoint.
 */
add_filter(
    'woocommerce_webhook_payload',
    'wc_inv_transform_refund_payload',
    20,   // after the order transform above
    4
);

function wc_inv_transform_refund_payload( $payload, $resource, $resource_id, $webhook_id ) {
    // Only modify already-transformed order payloads
    if ( ! isset( $payload['status'] ) ) {
        return $payload;
    }

    $status = $payload['status'] ?? '';

    // WooCommerce statuses: cancelled → 'cancel', refunded → 'refund'
    if ( 'cancelled' === $status ) {
        $payload['event_type'] = 'cancel';
    } elseif ( 'refunded' === $status ) {
        $payload['event_type'] = 'refund';
    }

    return $payload;
}
